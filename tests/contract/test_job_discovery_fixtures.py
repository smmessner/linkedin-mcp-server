"""Synthetic geography/freshness validation through production parsers and executor.

These fixtures assert extraction and query construction, not LinkedIn's live
filter accuracy or an eligibility decision that the MCP does not implement.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Literal, cast
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright
from pydantic import BaseModel

from linkedin_mcp.browser import BrowserManager
from linkedin_mcp.browser.pages import JobDetailPage, JobSearchPage
from linkedin_mcp.domain.models import (
    JobDetailInput,
    JobDetailObservation,
    JobSearchFilters,
    JobSearchInput,
    JobWorkplaceType,
    StopReason,
)
from linkedin_mcp.errors import ParserDriftError
from tests.contract.test_mcp_protocol import protocol_container
from tests.unit.test_job_pages import FixtureBrowser

ROOT = Path(__file__).parents[1] / "fixtures"


class Scenario(BaseModel):
    id: str
    location: str
    workplace: str
    title: str
    employer: str
    description: str
    eligibility: str
    compensation: str | None
    application_url: str | None


def scenarios() -> tuple[Scenario, ...]:
    data = json.loads((ROOT / "job_discovery/scenarios.json").read_text())
    assert data["provenance"] == "synthetic_only"
    assert data["contains_live_data"] is False
    return tuple(Scenario.model_validate(s) for s in data["scenarios"])


def card(s: Scenario, *, missing: bool = False) -> str:
    company = (
        ""
        if missing
        else f'<div class="artdeco-entity-lockup__subtitle">{escape(s.employer)}</div>'
    )
    location = (
        f'<div class="artdeco-entity-lockup__caption">{escape(s.location)} ({s.workplace})</div>'
    )
    return f'''<li data-occludable-job-id="{s.id}"><div data-job-id="{s.id}">
      <div class="artdeco-entity-lockup__title"><a class="job-card-list__title--link"
        href="/jobs/view/{s.id}/" aria-label="{escape(s.title)}">{escape(s.title)}</a></div>
      {company}{location}
      <ul class="job-card-list__footer-wrapper"><li>3 hours ago</li></ul>
    </div></li>'''


def search_html(cards: str, *, advertised: int) -> str:
    return f"""<html><body>
      <section aria-label="search filters"><h2>Jobs search</h2>
      <button aria-label="Show all filters">All filters</button></section>
      <main><header class="scaffold-layout__list-header"><span>{advertised} results</span></header>
      <ul>{cards}</ul><button aria-label="Page 1">1</button></main>
    </body></html>"""


def detail_html(s: Scenario) -> str:
    # Reuse the established semantic layout with entirely synthetic content.
    template = (ROOT / "linkedin/jobs/latest/detail-external-apply.html").read_text()
    return (
        template.replace("4100000002", s.id)
        .replace("Software Development Engineer", escape(s.title))
        .replace("Pixel Labs", escape(s.employer))
        .replace("Noida, Uttar Pradesh, India", escape(s.location))
        .replace(">On-site<", f">{s.workplace}<")
        .replace(
            "Build current web-platform capabilities for millions of customers.",
            escape(s.description),
        )
        .replace(
            '<button type="button" aria-label="Apply on company website">Apply</button>',
            f'<a href="{escape(s.application_url or "https://careers.example.org/unknown")}" '
            'aria-label="Apply on company website">Apply</a>',
        )
    )


@pytest.mark.timeout(40)
@pytest.mark.parametrize("freshness", [24, 168])
async def test_geographies_workplace_freshness_and_complete_descriptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, freshness: Literal[24, 168]
) -> None:
    container = protocol_container(tmp_path, access_mode="job-search-only")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", lambda route: route.abort())
        try:
            for s in scenarios():
                fixture = FixtureBrowser(page, search_html(card(s), advertised=1))
                monkeypatch.setattr(
                    container.executor,
                    "_job_search",
                    JobSearchPage(cast(BrowserManager, fixture), max_pages=1),
                )
                workplace = JobWorkplaceType(s.workplace.casefold().replace("-", "_"))
                request = JobSearchInput(
                    context_id="synthetic",
                    request_id=f"search-{s.id}-{freshness}",
                    location=s.location,
                    freshness_hours=freshness,
                    filters=JobSearchFilters(workplace_types=(workplace,)),
                )
                output = await container.executor.search_jobs(request)
                assert [j.job_id for j in output.jobs] == [s.id]
                job = output.jobs[0]
                assert (job.title, job.company_name, job.location, job.workplace_type) == (
                    s.title,
                    s.employer,
                    s.location,
                    workplace,
                )
                query = parse_qs(urlsplit(fixture.navigations[0]).query)
                assert query["location"] == [s.location]
                assert query["f_TPR"] == [f"r{freshness * 3600}"]
                assert query["f_WT"] == [
                    {"Remote": "2", "Hybrid": "3", "On-site": "1"}[s.workplace]
                ]
                fixture.html = detail_html(s)
                monkeypatch.setattr(
                    container.executor, "_job_detail", JobDetailPage(cast(BrowserManager, fixture))
                )
                detail = await container.executor.get_job(
                    JobDetailInput(
                        context_id="synthetic",
                        request_id=f"detail-{s.id}-{freshness}",
                        job_id=s.id,
                    )
                )
                assert detail.job.description_text == s.description
                assert (detail.job.company_name, detail.job.title, detail.job.location) == (
                    s.employer,
                    s.title,
                    s.location,
                )
                assert str(detail.job.job_url) == f"https://www.linkedin.com/jobs/view/{s.id}/"
                if s.compensation:
                    assert detail.job.description_text is not None
                    assert s.compensation in detail.job.description_text
                # Characterize gaps: raw text exists without typed fields or decisions.
                assert (
                    not {"compensation", "application_url", "washington_eligible"}
                    & JobDetailObservation.model_fields.keys()
                )
                assert len(detail.sources) == 1
        finally:
            await browser.close()
            await container.close()


@pytest.mark.timeout(30)
async def test_duplicates_missing_information_and_cursor_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = scenarios()[:2]
    container = protocol_container(tmp_path, access_mode="job-search-only")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", lambda route: route.abort())
        fixture = FixtureBrowser(
            page, search_html(card(first) + card(first) + card(second, missing=True), advertised=2)
        )
        monkeypatch.setattr(
            container.executor,
            "_job_search",
            JobSearchPage(cast(BrowserManager, fixture), max_pages=1),
        )
        try:
            request = JobSearchInput(context_id="synthetic", request_id="page-1", page_size=1)
            with pytest.raises(ParserDriftError, match="duplicate result-card identities"):
                await container.executor.search_jobs(request)
            fixture.html = search_html(card(first) + card(second, missing=True), advertised=2)
            request = request.model_copy(update={"request_id": "valid-page-1"})
            a = await container.executor.search_jobs(request)
            assert [j.job_id for j in a.jobs] == [first.id]
            assert a.pagination.has_more and a.pagination.next_cursor
            b = await container.executor.search_jobs(
                request.model_copy(
                    update={"request_id": "page-2", "cursor": a.pagination.next_cursor}
                )
            )
            assert [j.job_id for j in b.jobs] == [second.id]
            assert b.jobs[0].company_name is None
            assert b.jobs[0].location == second.location
            assert not b.pagination.has_more
            assert not b.pagination.truncated
            assert b.coverage.stop_reason == StopReason.VISIBLE_PAGE_COMPLETE
        finally:
            await browser.close()
            await container.close()


@pytest.mark.timeout(25)
async def test_incomplete_inventory_is_truncated_in_restricted_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = protocol_container(tmp_path, access_mode="job-search-only")
    s = scenarios()[0]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", lambda route: route.abort())
        fixture = FixtureBrowser(page, search_html(card(s), advertised=4))
        try:
            _, coverage, _, _ = await JobSearchPage(
                cast(BrowserManager, fixture), max_pages=1
            ).collect(JobSearchInput(context_id="synthetic", request_id="incomplete"))
            # Existing limitation, not acceptance: absent next control masks a mismatch.
            assert coverage.stop_reason == StopReason.VISIBLE_PAGE_COMPLETE
            assert coverage.advertised_result_count == 4
            assert coverage.result_count == 1
            monkeypatch.setattr(
                container.executor,
                "_job_search",
                JobSearchPage(cast(BrowserManager, fixture), max_pages=1),
            )
            result = await container.executor.search_jobs(
                JobSearchInput(context_id="synthetic", request_id="restricted-incomplete")
            )
            assert result.coverage.stop_reason == StopReason.SAFETY_BOUND
            assert result.pagination.truncated is True
            assert result.pagination.has_more is False
            assert result.pagination.next_cursor is None
        finally:
            await browser.close()
            await container.close()


@pytest.mark.timeout(25)
async def test_missing_required_visible_location_fails_closed() -> None:
    s = scenarios()[0]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", lambda route: route.abort())
        try:
            html = search_html(card(s), advertised=1)
            html = html.replace(
                f'<div class="artdeco-entity-lockup__caption">{escape(s.location)} '
                f"({s.workplace})</div>",
                "",
            )
            await page.set_content(html)
            with pytest.raises(ParserDriftError, match="visible location"):
                await JobSearchPage.extract_visible_jobs(page)
        finally:
            await browser.close()
