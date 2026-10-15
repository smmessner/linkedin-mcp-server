"""Conservative normalization: unknown values are deliberate results."""

from __future__ import annotations

from decimal import Decimal

import pytest

from linkedin_mcp.domain.job_metadata import (
    compensation_from_text,
    external_application_url,
    posting_from_visible,
    washington_from_description,
)


@pytest.mark.parametrize(
    ("text", "low", "high", "currency", "period"),
    (
        ("Salary: USD 140,000 - USD 180,000 per year", "140000", "180000", "USD", "year"),
        ("Pay: CAD 40.50 - 50.75/hour", "40.50", "50.75", "CAD", "hour"),
        ("EUR 4K to 5K per month", "4000", "5000", "EUR", "month"),
        ("GBP 700 per week", "700", "700", "GBP", "week"),
        ("₹1000 - ₹2000 per day", "1000", "2000", "INR", "day"),
        ("$140,000 - $180,000/year", "140000", "180000", None, "year"),
        ("up to USD 200K per year", None, "200000", "USD", "year"),
        ("from USD 100K per year", "100000", None, "USD", "year"),
    ),
)
def test_explicit_pay_ranges(
    text: str, low: str | None, high: str | None, currency: str | None, period: str
) -> None:
    offers = compensation_from_text(text)
    assert len(offers) == 1
    offer = offers[0]
    assert offer.minimum == (Decimal(low) if low else None)
    assert offer.maximum == (Decimal(high) if high else None)
    assert offer.currency == currency
    assert offer.pay_period == period
    assert offer.raw_text in text
    if currency is None:
        assert "currency_missing_or_ambiguous" in offer.uncertainties


@pytest.mark.parametrize(
    "text",
    ("", "5-10 years experience", "Posted 2026-10-01", "Competitive salary", "140000 per year"),
)
def test_no_invented_compensation(text: str) -> None:
    assert compensation_from_text(text) == ()


@pytest.mark.parametrize(
    "text", ("USD 200K - USD 100K/year", "USD 100K - CAD 200K/year", "USD 100 - USD 200K/year")
)
def test_ambiguous_or_conflicting_ranges_are_unknown(text: str) -> None:
    offer = compensation_from_text(text)[0]
    assert offer.minimum is None and offer.maximum is None
    assert offer.uncertainties


def test_multiple_contextual_ranges_stay_separate() -> None:
    offers = compensation_from_text("WA: USD 100K - 120K/year\nCA: USD 130K - 150K/year")
    assert len(offers) == 2
    assert [o.minimum for o in offers] == [Decimal(100000), Decimal(130000)]
    assert all("multiple_ranges_require_context" in o.uncertainties for o in offers)
    assert (
        "qualified_or_non_base_pay"
        in compensation_from_text("Estimated bonus: USD 10K/year")[0].uncertainties
    )


@pytest.mark.parametrize(
    ("text", "complete", "status"),
    (
        ("Remote employment is available to residents of Washington state.", True, "eligible"),
        ("Washington residents are eligible.", True, "eligible"),
        ("Washington residents are not eligible.", True, "excluded"),
        ("We cannot hire Washington residents.", True, "excluded"),
        (
            "Washington residents are eligible. We cannot hire Washington residents.",
            True,
            "unknown",
        ),
        ("Washington residents may be eligible.", True, "unknown"),
        ("Washington residents are eligible, subject to approval.", True, "unknown"),
        ("Washington residents are not ineligible.", True, "unknown"),
        ("Washington residents are eligible.", False, "unknown"),
        ("Remote role, United States.", True, "unknown"),
        ("Washington salary disclosure USD 100000/year.", True, "unknown"),
        ("Employment is available in Washington, DC.", True, "unknown"),
        ("Only California residents can work here.", True, "unknown"),
        ("", True, "unknown"),
    ),
)
def test_washington_requires_unconditional_description_evidence(
    text: str, complete: bool, status: str
) -> None:
    result = washington_from_description(text, complete=complete)
    assert result.status == status
    assert all(e.quote in text for e in result.evidence)
    if status != "unknown":
        assert result.evidence


@pytest.mark.parametrize(
    "url",
    (
        "https://careers.example.org/jobs/123?source=linkedin",
        "https://boards.greenhouse.io/example/jobs/123",
        "https://jobs.lever.co/example/abc",
    ),
)
def test_application_destination_is_returned_without_resolution(url: str) -> None:
    assert external_application_url(url) == url


@pytest.mark.parametrize(
    "url",
    (
        "http://example.org/job",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "/apply/123",
        "https://www.linkedin.com/redir?url=https://example.org",
        "https://linkedin.com/jobs/123",
        "https://user:secret@example.org/job",
        "https://127.0.0.1/job",
        "https://[::1]/job",
        "https://169.254.169.254/job",
        "https://example.local/job",
        "https://localhost/job",
        "https://example.org:8443/job",
        "https://example.org/a\nb",
        "https://example.org\\@private/job",
        "https://bad%2eexample.org/job",
        "https://example.org/" + "x" * 2048,
    ),
)
def test_unsafe_ambiguous_or_linkedin_destinations_are_not_unwrapped(url: str) -> None:
    assert external_application_url(url) is None


def test_explicit_timestamps_and_reposts_are_distinct() -> None:
    result = posting_from_visible(
        "Reposted 3 hours ago",
        (
            ("Originally posted", "2026-09-01T12:00:00Z"),
            ("Reposted 3 hours ago", "2026-10-15T10:00:00-07:00"),
        ),
    )
    assert result.posted_at is not None and result.posted_at.month == 9
    assert result.reposted_at is not None and result.reposted_at.month == 10
    assert result.is_repost is True
    assert "repost_is_not_new_job" in result.uncertainties


@pytest.mark.parametrize("text", ("3 hours ago", "Reposted 1 day ago", ""))
def test_relative_text_never_creates_an_absolute_timestamp(text: str) -> None:
    result = posting_from_visible(text)
    assert result.posted_at is None and result.reposted_at is None and result.posted_date is None


def test_dates_missing_timezones_conflicts_and_invalid_values() -> None:
    assert str(posting_from_visible("Posted on 2026-10-01").posted_date) == "2026-10-01"
    for values in (
        (("Posted", "2026-10-01T10:00:00"),),
        (("Posted", "invalid"),),
        (("Posted", "2026-01-01T00:00:00Z"), ("Posted", "2026-02-01T00:00:00Z")),
    ):
        result = posting_from_visible("Posted", values)
        assert result.posted_at is None and result.uncertainties
    assert posting_from_visible("Updated", (("Updated", "2026-10-01T10:00:00Z"),)).posted_at is None


@pytest.mark.parametrize(
    "text",
    (
        "Washington residents are eligible for health insurance.",
        "Washington residents are eligible except those living in Seattle.",
        "Washington residents are eligible if approved by HR.",
    ),
)
def test_benefits_or_conditional_statements_do_not_prove_employment(text: str) -> None:
    assert washington_from_description(text, complete=True).status == "unknown"


def test_ambiguous_regional_number_format_and_date_conflict() -> None:
    offer = compensation_from_text("EUR 40.000,00 - EUR 50.000,00/year")[0]
    assert offer.minimum is None and offer.maximum is None
    assert "ambiguous_number_format" in offer.uncertainties
    posted = posting_from_visible("Posted on 2026-01-01", (("Posted", "2026-02-01T00:00:00Z"),))
    assert posted.posted_at is None and posted.posted_date is None
    assert "conflicting_timestamps" in posted.uncertainties


def test_explicit_timestamp_attribute_provenance_is_preserved() -> None:
    result = posting_from_visible(
        "Posted 3 hours ago", (("Posted 3 hours ago", "2026-10-15T12:00:00Z"),)
    )
    assert result.timestamp_attributes[0].raw_datetime == "2026-10-15T12:00:00Z"
    assert result.timestamp_attributes[0].visible_label == "Posted 3 hours ago"
