"""Synthetic DOM, cursor, interruption, and dedicated-startup regressions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import Page, async_playwright
from pydantic import ValidationError

from linkedin_mcp import __main__ as cli
from linkedin_mcp.browser import BrowserManager
from linkedin_mcp.browser.pages import JobDetailPage, JobSearchPage
from linkedin_mcp.config import JobDiscoverySettings
from linkedin_mcp.domain.evidence import source_from_job_detail
from linkedin_mcp.domain.models import JobDetailInput, JobSearchInput, StopReason
from linkedin_mcp.errors import BrowserUnavailableError, ParserDriftError
from tests.contract.test_job_discovery_fixtures import card, detail_html, scenarios, search_html
from tests.contract.test_mcp_protocol import protocol_container
from tests.unit.test_job_pages import FixtureBrowser


@pytest.fixture
async def offline_page() -> AsyncGenerator[Page]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", lambda route: route.abort())
        try:
            yield page
        finally:
            await browser.close()


async def test_detail_metadata_roundtrip_and_no_application_click(offline_page: Page) -> None:
    s = scenarios()[0]
    html = detail_html(s).replace("3 hours ago", "3 hours ago")
    html = html.replace(
        "2 days ago", '<time datetime="2026-10-15T12:00:00Z">Posted 3 hours ago</time>'
    )
    fixture = FixtureBrowser(offline_page, html)
    job = await JobDetailPage(cast(BrowserManager, fixture)).read(
        JobDetailInput(context_id="fixture", request_id="detail-v2", job_id=s.id)
    )
    assert job.description_completeness == "complete"
    assert job.washington_eligibility.status == "eligible"
    assert str(job.application_url) == s.application_url
    assert str(job.job_url) != str(job.application_url)
    assert job.application_url_source_href == s.application_url
    assert job.posting.posted_at is not None
    assert job.compensation[0].currency == "USD"
    assert job.compensation[0].minimum == 140000
    assert len(fixture.navigations) == 1
    assert offline_page.url == "about:blank"
    source = source_from_job_detail(job)
    assert source.content["application_url"] == s.application_url
    assert job.application_url_evidence[0].quote in job.visible_text
    assert job.washington_eligibility.evidence[0].quote in job.description_text  # pyright: ignore[reportOperatorIssue]


@pytest.mark.parametrize(
    ("content", "status"),
    (
        ('<div data-testid="expandable-text-box">Description unavailable.</div>', "unavailable"),
        ('<div data-testid="expandable-text-box"></div>', "unknown"),
        ('<div data-testid="expandable-text-box" aria-busy="true">Loading text</div>', "unknown"),
        (
            '<div data-testid="expandable-text-box" style="height:10px;overflow:hidden">'
            "Line one<br>Line two</div>",
            "incomplete",
        ),
        (
            '<div data-testid="expandable-text-box">Partial... more'
            '<button data-testid="expandable-text-button">more</button></div>',
            "incomplete",
        ),
        ('<div data-testid="expandable-text-box">Full rendered description.</div>', "complete"),
    ),
)
async def test_description_completeness_states(
    offline_page: Page, content: str, status: str
) -> None:
    from linkedin_mcp.browser.pages.job_enrichment import description_state

    await offline_page.set_content(content)
    _, actual, reason = await description_state(offline_page.locator("div"))
    assert actual == status
    assert reason


async def test_missing_description_is_unknown_and_failed_expansion_is_incomplete(
    offline_page: Page,
) -> None:
    s = scenarios()[0]
    html = detail_html(s)
    start = html.index('<section id="about-job">')
    missing = html[:start] + "</section></main></body></html>"
    await offline_page.set_content(missing)
    job = await JobDetailPage.extract_visible_job(offline_page, s.id)
    assert job.description_text is None and job.description_completeness == "unknown"
    html = html.replace(
        "Final complete description sentence.",
        '<button data-testid="expandable-text-button">more</button>',
    )
    fixture = FixtureBrowser(offline_page, html)
    job = await JobDetailPage(cast(BrowserManager, fixture)).read(
        JobDetailInput(context_id="fixture", request_id="partial", job_id=s.id)
    )
    assert job.description_completeness == "incomplete"
    assert job.washington_eligibility.status == "unknown"


@pytest.mark.parametrize(
    ("links", "status"),
    (
        (
            '<a href="https://careers.example.org/one">Apply</a><a href="https://careers.example.org/two">Apply</a>',
            "ambiguous",
        ),
        ('<a href="https://user:password@example.org">Apply</a>', "rejected"),
        ("<button onclick=\"throw Error('must not click')\">Apply</button>", "unknown"),
        ('<a hidden href="https://careers.example.org">Apply</a>', "unknown"),
        (
            '<a href="https://www.linkedin.com/redirect?url=https://careers.example.org">Apply</a>',
            "rejected",
        ),
    ),
)
async def test_application_links_remain_unknown_when_not_safe_and_unique(
    offline_page: Page, links: str, status: str
) -> None:
    from linkedin_mcp.browser.pages.job_enrichment import visible_application

    await offline_page.set_content(f"<main>{links}</main>")
    url, actual, evidence = await visible_application(offline_page.locator("main"))
    assert url is None and actual == status and evidence == ()


class ChangingBrowser(FixtureBrowser):
    def __init__(self, page: Page, pages: tuple[str, ...], *, interrupt: bool = False) -> None:
        super().__init__(page, pages[0])
        self.pages = pages
        self.interrupt = interrupt

    async def navigate(self, page: Page, url: str) -> None:
        self.navigations.append(url)
        index = int(parse_qs(urlsplit(url).query).get("start", ["0"])[0]) // 25
        if self.interrupt and index > 0:
            raise BrowserUnavailableError("Synthetic interruption")
        await page.set_content(self.pages[min(index, len(self.pages) - 1)])


def next_page(html: str) -> str:
    return html.replace("</main>", '<button aria-label="View next page">Next</button></main>')


@pytest.mark.parametrize(
    "case",
    (
        "count_changed",
        "count_missing",
        "lower_bound",
        "incomplete",
        "loader",
        "tail",
        "interrupted",
    ),
)
async def test_collection_never_claims_unproven_inventory(offline_page: Page, case: str) -> None:
    a, b = scenarios()[:2]
    first = next_page(search_html(card(a), advertised=2))
    second = search_html(card(b), advertised=2)
    if case == "count_changed":
        second = search_html(card(b), advertised=3)
    elif case == "count_missing":
        second = second.replace("2 results", "Jobs")
    elif case == "lower_bound":
        second = second.replace("2 results", "2+ results")
    elif case == "incomplete":
        second = search_html(card(b), advertised=4)
    elif case == "loader":
        second = second.replace("</main>", '<div aria-busy="true">Loading</div></main>')
    elif case == "tail":
        second = second.replace("</main>", "<button>Load more</button></main>")
    fixture = ChangingBrowser(offline_page, (first, second), interrupt=case == "interrupted")
    jobs, coverage, _, _ = await JobSearchPage(
        cast(BrowserManager, fixture), max_pages=3, verify_inventory=True
    ).collect(JobSearchInput(context_id="fixture", request_id=case, page_size=10))
    assert jobs
    assert coverage.inventory_status in {"uncertain", "interrupted"}
    assert coverage.stop_reason == StopReason.SAFETY_BOUND
    assert coverage.inventory_issues


async def test_reconciled_pages_and_duplicates_across_pages(offline_page: Page) -> None:
    a, b = scenarios()[:2]
    fixture = ChangingBrowser(
        offline_page,
        (
            next_page(search_html(card(a), advertised=2)),
            search_html(card(a) + card(b), advertised=2),
        ),
    )
    jobs, coverage, _, _ = await JobSearchPage(
        cast(BrowserManager, fixture), max_pages=2, verify_inventory=True
    ).collect(JobSearchInput(context_id="fixture", request_id="duplicates", page_size=10))
    assert [j.job_id for j in jobs] == [a.id, b.id]
    assert coverage.duplicate_count == 1
    assert coverage.inventory_status == "reconciled"


@pytest.mark.parametrize("change", ("count", "membership"))
async def test_cursor_churn_is_terminal_truncation(
    offline_page: Page, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    a, b, c = scenarios()[:3]
    container = protocol_container(tmp_path, access_mode="job-search-only")
    fixture = FixtureBrowser(offline_page, search_html(card(a) + card(b), advertised=2))
    monkeypatch.setattr(
        container.executor,
        "_job_search",
        JobSearchPage(cast(BrowserManager, fixture), max_pages=2, verify_inventory=True),
    )
    try:
        request = JobSearchInput(context_id="fixture", request_id="first", page_size=1)
        first = await container.executor.search_jobs(request)
        assert first.pagination.next_cursor
        fixture.html = (
            search_html(card(a) + card(b), advertised=3)
            if change == "count"
            else search_html(card(c) + card(b), advertised=2)
        )
        result = await container.executor.search_jobs(
            request.model_copy(
                update={"request_id": "second", "cursor": first.pagination.next_cursor}
            )
        )
        assert result.pagination.truncated
        assert not result.pagination.has_more
        assert result.coverage.inventory_issues
        assert result.coverage.stop_reason == StopReason.SAFETY_BOUND
    finally:
        await container.close()


async def test_interruption_before_results_and_cancellation_are_errors(offline_page: Page) -> None:
    fixture = FixtureBrowser(offline_page, "")

    async def cancelled(page: Page, url: str) -> None:
        raise asyncio.CancelledError

    fixture.navigate = cancelled
    with pytest.raises(asyncio.CancelledError):
        await JobSearchPage(
            cast(BrowserManager, fixture), max_pages=1, verify_inventory=True
        ).collect(JobSearchInput(context_id="fixture", request_id="cancelled"))

    async def failed(page: Page, url: str) -> None:
        raise BrowserUnavailableError("Synthetic first-page interruption")

    fixture.navigate = failed
    with pytest.raises(BrowserUnavailableError):
        await JobSearchPage(
            cast(BrowserManager, fixture), max_pages=1, verify_inventory=True
        ).collect(JobSearchInput(context_id="fixture", request_id="failed"))


def test_discovery_settings_require_explicit_restriction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LINKEDIN_MCP_ACCESS_MODE", raising=False)
    with pytest.raises(ValidationError, match="requires explicit"):
        JobDiscoverySettings(_env_file=None)  # pyright: ignore[reportCallIssue]
    for invalid in ("standard", "", "false", "job-search"):
        monkeypatch.setenv("LINKEDIN_MCP_ACCESS_MODE", invalid)
        with pytest.raises(ValidationError):
            JobDiscoverySettings()
    monkeypatch.setenv("LINKEDIN_MCP_ACCESS_MODE", "job-search-only")
    assert JobDiscoverySettings().auto_login_on_start is False
    with pytest.raises(ValidationError, match="only stdio"):
        JobDiscoverySettings(transport="streamable-http")


def test_dedicated_launcher_cannot_start_standard_or_background_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import linkedin_mcp.container as container_module
    import linkedin_mcp.server as server_module

    monkeypatch.chdir(tmp_path)
    run = Mock()
    build = Mock(return_value=object())
    monkeypatch.setattr(container_module, "create_production_container", build)

    def fake_server(_: object) -> SimpleNamespace:
        return SimpleNamespace(run=run)

    monkeypatch.setattr(server_module, "create_mcp_server", fake_server)
    monkeypatch.delenv("LINKEDIN_MCP_ACCESS_MODE", raising=False)
    with pytest.raises(SystemExit) as error:
        cli.discovery_main()
    assert error.value.code == 2
    build.assert_not_called()
    monkeypatch.setenv("LINKEDIN_MCP_ACCESS_MODE", "job-search-only")
    cli.discovery_main()
    run.assert_called_once_with(transport="stdio")
    assert build.call_args.args[0].access_mode == "job-search-only"


async def test_new_metadata_roundtrips_through_official_mcp(
    offline_page: Page, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.contract.test_mcp_protocol import protocol_session

    s = scenarios()[0]
    fixture = FixtureBrowser(offline_page, detail_html(s))
    container = protocol_container(tmp_path, access_mode="job-search-only")
    monkeypatch.setattr(
        container.executor, "_job_detail", JobDetailPage(cast(BrowserManager, fixture))
    )
    async with protocol_session(
        tmp_path, access_mode="job-search-only", app_container=container
    ) as session:
        response = await session.call_tool(
            "linkedin.jobs.get", {"context_id": "fixture", "request_id": "metadata", "job_id": s.id}
        )
        assert not response.isError and response.structuredContent
        job = response.structuredContent["job"]
        assert job["compensation"][0]["minimum"] == "140000"
        assert job["compensation"][0]["maximum"] == "180000"
        assert job["compensation"][0]["pay_period"] == "year"
        assert job["application_url"] == s.application_url
        assert job["washington_eligibility"]["status"] == "eligible"
        assert job["description_completeness"] == "complete"
        assert job["posting"]["posted_at"] is None


@pytest.mark.parametrize("case", ("zero", "no_header", "page_bound", "transient_zero"))
async def test_empty_counts_neighbors_and_bounds_are_conservative(
    offline_page: Page, case: str
) -> None:
    s = scenarios()[0]
    if case == "zero":
        html = search_html("", advertised=0)
    elif case == "no_header":
        html = search_html(card(s), advertised=1).replace(
            'class="scaffold-layout__list-header"', 'class="neighbor"'
        )
    elif case == "page_bound":
        html = next_page(search_html(card(s), advertised=2))
    else:
        html = search_html(card(s), advertised=0)
    fixture = FixtureBrowser(offline_page, html)
    _, coverage, _, _ = await JobSearchPage(
        cast(BrowserManager, fixture), max_pages=1, verify_inventory=True
    ).collect(JobSearchInput(context_id="fixture", request_id=case, page_size=10))
    if case == "zero":
        assert coverage.inventory_status == "reconciled"
        assert coverage.result_count == 0
        assert coverage.stop_reason == StopReason.NO_NEW_RESULTS
    else:
        assert coverage.inventory_status == "uncertain"
        assert coverage.inventory_issues
        assert coverage.stop_reason == StopReason.SAFETY_BOUND


async def test_raw_dom_bound_rejects_unclassified_inventory(offline_page: Page) -> None:
    await offline_page.set_content(
        "<main>"
        + "".join(f'<li data-occludable-job-id="{4300000000 + i}"></li>' for i in range(501))
        + "</main>"
    )
    with pytest.raises(ParserDriftError, match="raw-card inventory"):
        await JobSearchPage.extract_visible_jobs(offline_page)


async def test_cursor_cancellation_releases_reservation_for_retry(
    offline_page: Page, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, c = scenarios()[:3]
    container = protocol_container(tmp_path, access_mode="job-search-only")
    fixture = FixtureBrowser(offline_page, search_html(card(a) + card(b) + card(c), advertised=3))
    provider = JobSearchPage(cast(BrowserManager, fixture), max_pages=1, verify_inventory=True)
    monkeypatch.setattr(container.executor, "_job_search", provider)
    try:
        request = JobSearchInput(context_id="fixture", request_id="begin", page_size=1)
        first = await container.executor.search_jobs(request)
        assert first.pagination.next_cursor
        real_navigate = fixture.navigate

        async def cancelled(page: Page, url: str) -> None:
            raise asyncio.CancelledError

        fixture.navigate = cancelled
        continuation = request.model_copy(
            update={"cursor": first.pagination.next_cursor, "request_id": "cancel"}
        )
        with pytest.raises(asyncio.CancelledError):
            await container.executor.search_jobs(continuation)
        fixture.navigate = real_navigate
        result = await container.executor.search_jobs(
            continuation.model_copy(update={"request_id": "retry"})
        )
        assert [job.job_id for job in result.jobs] == [b.id]
        assert not result.pagination.truncated
    finally:
        await container.close()


async def test_enrichment_evidence_cannot_be_forged(offline_page: Page) -> None:
    from linkedin_mcp.domain.models import EvidenceField, WashingtonEligibility

    s = scenarios()[0]
    await offline_page.set_content(detail_html(s))
    job = await JobDetailPage.extract_visible_job(offline_page, s.id)
    forged = job.model_copy(
        update={
            "washington_eligibility": WashingtonEligibility(
                status="eligible",
                evidence=(EvidenceField(field="washington_eligibility", quote=job.title),),
            )
        }
    )
    with pytest.raises(ParserDriftError, match="not from the description"):
        source_from_job_detail(forged)


async def test_bounded_link_and_timestamp_inventories_do_not_select_a_prefix(
    offline_page: Page,
) -> None:
    from linkedin_mcp.browser.pages.job_enrichment import visible_application, visible_posting

    await offline_page.set_content(
        "<main>"
        + "".join(
            f'<a href="https://careers.example.org/{i}">Apply</a>'
            f'<time datetime="2026-10-01T00:00:00Z">Posted {i} hours ago</time>'
            for i in range(21)
        )
        + "</main>"
    )
    root = offline_page.locator("main")
    url, status, _ = await visible_application(root)
    assert url is None and status == "ambiguous"
    posting = await visible_posting(root, await root.inner_text())
    assert posting.posted_at is None and posting.uncertainties == ("timestamp_inventory_bound",)


async def test_nested_description_clipping_prevents_complete_claim(offline_page: Page) -> None:
    from linkedin_mcp.browser.pages.job_enrichment import description_state

    await offline_page.set_content(
        '<div id="description"><p style="height:5px;overflow:hidden">First<br>Second</p></div>'
    )
    _, state, _ = await description_state(offline_page.locator("#description"))
    assert state == "incomplete"


async def test_missing_card_identity_is_not_silently_discarded(offline_page: Page) -> None:
    await offline_page.set_content('<main><li data-occludable-job-id=""></li></main>')
    with pytest.raises(ParserDriftError, match="missing result-card identity"):
        await JobSearchPage.extract_visible_jobs(offline_page)


async def test_empty_lower_bound_observed_after_initial_count_is_uncertain(
    offline_page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    import linkedin_mcp.browser.pages.jobs as jobs_module

    fixture = FixtureBrowser(offline_page, search_html("", advertised=0))
    samples = iter(((0, False), (0, True), (0, False), (0, False)))

    async def count(page: Page, *, selected_only: bool = False) -> tuple[int | None, bool]:
        return next(samples)

    monkeypatch.setattr(jobs_module, "_job_search_result_count", count)
    _, coverage, _, _ = await JobSearchPage(
        cast(BrowserManager, fixture), max_pages=1, verify_inventory=True
    ).collect(JobSearchInput(context_id="fixture", request_id="empty-lower-bound"))
    assert coverage.inventory_status == "uncertain"
    assert coverage.stop_reason == StopReason.SAFETY_BOUND
    assert "advertised_count_is_lower_bound" in coverage.inventory_issues


@pytest.mark.parametrize(
    "marker",
    (
        '<div aria-busy="true"></div>',
        '<div role="progressbar"></div>',
        "<button>Load more</button>",
    ),
)
async def test_zero_count_with_pending_inventory_is_not_complete(
    offline_page: Page, marker: str
) -> None:
    fixture = FixtureBrowser(
        offline_page, search_html("", advertised=0).replace("</main>", marker + "</main>")
    )
    _, coverage, _, _ = await JobSearchPage(
        cast(BrowserManager, fixture), max_pages=1, verify_inventory=True
    ).collect(JobSearchInput(context_id="fixture", request_id="pending-empty"))
    assert coverage.inventory_status == "uncertain"
    assert coverage.stop_reason == StopReason.SAFETY_BOUND


async def test_competing_selected_header_counts_are_unknown(offline_page: Page) -> None:
    a = scenarios()[0]
    fixture = FixtureBrowser(
        offline_page,
        search_html(card(a), advertised=1).replace("1 results", "1 results / 9 results"),
    )
    _, coverage, _, _ = await JobSearchPage(
        cast(BrowserManager, fixture), max_pages=1, verify_inventory=True
    ).collect(JobSearchInput(context_id="fixture", request_id="ambiguous-header"))
    assert coverage.advertised_result_count is None
    assert coverage.inventory_status == "uncertain"
    assert "advertised_count_unavailable" in coverage.inventory_issues


async def test_loading_during_description_stability_check_is_unknown(
    offline_page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    from linkedin_mcp.browser.pages.job_enrichment import description_state

    await offline_page.set_content('<div id="description">Visible text</div>')
    # Trigger on the stability wait itself, avoiding a timing race with initial locator calls.
    original_wait = offline_page.wait_for_timeout

    async def start_loading(delay_ms: float) -> None:
        await offline_page.locator("#description").evaluate(
            "node => node.setAttribute('aria-busy', 'true')"
        )
        await original_wait(delay_ms)

    monkeypatch.setattr(offline_page, "wait_for_timeout", start_loading)
    _, state, reason = await description_state(offline_page.locator("#description"))
    assert state == "unknown"
    assert "loading" in reason
