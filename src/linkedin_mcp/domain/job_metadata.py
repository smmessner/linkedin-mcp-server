"""Conservative normalization of visible job text; never fetches a destination."""

from __future__ import annotations

import ipaddress
import re
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlsplit

from linkedin_mcp.domain.models import (
    EvidenceField,
    JobCompensation,
    JobPosting,
    JobTimestampAttribute,
    WashingtonEligibility,
)

_MONEY = r"(?:(?:USD|CAD|AUD|NZD|EUR|GBP|INR|JPY)\s*|[$€£₹])?\s*\d[\d,]*(?:\.\d+)?\s*[kK]?"
_PAY = re.compile(
    rf"(?P<qualifier>from\s+|up to\s+)?(?P<low>{_MONEY})"
    rf"(?:\s*(?:[-\u2013\u2014]|to)\s*(?P<high>{_MONEY}))?"
    r"\s*(?:(?:per\s+|/\s*|a\s+)(?P<period>hour|hr|day|week|month|year|yr)|(?P<annual>annually|annual|hourly))?",
    re.I,
)
_CURRENCY = re.compile(r"\b(?:USD|CAD|AUD|NZD|EUR|GBP|INR|JPY)\b", re.I)


def compensation_from_text(text: str) -> tuple[JobCompensation, ...]:
    offers: list[JobCompensation] = []
    for line in text.splitlines():
        # A money marker is mandatory: experience ranges and dates are not pay.
        if not re.search(r"[$€£₹]|\b(?:USD|CAD|AUD|NZD|EUR|GBP|INR|JPY)\b", line, re.I):
            continue
        for match in _PAY.finditer(line):
            raw = match.group().strip()
            if not re.search(r"[$€£₹]|\b(?:USD|CAD|AUD|NZD|EUR|GBP|INR|JPY)\b", raw, re.I):
                continue
            codes = {c.upper() for c in _CURRENCY.findall(raw)}
            codes.update(
                code for symbol, code in (("€", "EUR"), ("£", "GBP"), ("₹", "INR")) if symbol in raw
            )
            currency = next(iter(codes)) if len(codes) == 1 else None
            period = match.group("period") or match.group("annual")
            period = {
                "yr": "year",
                "hr": "hour",
                "annual": "year",
                "annually": "year",
                "hourly": "hour",
            }.get((period or "").lower(), (period or "").lower()) or None

            def amount(value: str) -> Decimal:
                number = re.search(r"\d[\d,]*(?:\.\d+)?", value)
                assert number is not None
                return Decimal(number.group().replace(",", "")) * (
                    1000 if re.search(r"[kK]\s*$", value) else 1
                )

            low = amount(match.group("low"))
            high = amount(match.group("high")) if match.group("high") else None
            qualifier = (match.group("qualifier") or "").strip().lower()
            if high is None and not qualifier:
                high = low  # A single explicit rate is a point, not an invented range.
            minimum, maximum = (None, low) if qualifier == "up to" else (low, high)
            issues: list[str] = []
            if re.match(r"\s*(?:million|billion|m\b|b\b)", line[match.end() :], re.I):
                minimum = maximum = None
                issues.append("unsupported_scale")
            if currency is None:
                issues.append("currency_missing_or_ambiguous")
            if period is None:
                issues.append("pay_period_unspecified")
            if maximum is None:
                issues.append("upper_bound_unspecified")
            if minimum is None:
                issues.append("lower_bound_unspecified")
            if (
                match.group("high")
                and min(low, high or low) < 1000
                and bool(re.search(r"[kK]\s*$", match.group("low")))
                != bool(re.search(r"[kK]\s*$", match.group("high")))
            ):
                minimum = maximum = None
                issues.append("ambiguous_scale")
            if len(codes) > 1 or (
                minimum is not None and maximum is not None and minimum > maximum
            ):
                minimum = maximum = None
                issues.append("conflicting_range")
            if re.search(r"\d+\.\d{3}(?:\D|$)|\d+,\d{1,2}(?:\D|$)", line):
                minimum = maximum = None
                issues.append("ambiguous_number_format")
            if re.search(
                r"\b(?:not|example|estimated|estimate|potential|bonus|equity|ote)\b", line, re.I
            ):
                issues.append("qualified_or_non_base_pay")
            offers.append(
                JobCompensation(
                    minimum=minimum,
                    maximum=maximum,
                    currency=currency,
                    pay_period=period,  # pyright: ignore[reportArgumentType]
                    raw_text=raw,
                    uncertainties=tuple(issues),
                )
            )  # pyright: ignore[reportArgumentType]
    offers = list(dict.fromkeys(offers))
    if len(offers) > 1:
        offers = [
            offer.model_copy(
                update={"uncertainties": (*offer.uncertainties, "multiple_ranges_require_context")}
            )
            for offer in offers
        ]
    return tuple(offers)


def washington_from_description(text: str | None, *, complete: bool) -> WashingtonEligibility:
    if not text:
        return WashingtonEligibility()
    positive: list[str] = []
    negative: list[str] = []
    ambiguous: list[str] = []
    # Only explicit state-employment sentences; locations, salary disclosures,
    # benefit notices, and the word "remote" do not establish eligibility.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if not re.search(r"\bWashington(?: state)?\b|\bWA\b", sentence, re.I):
            continue
        if re.search(r"Washington\s*,?\s*(?:D\.?C\.?|District of Columbia)", sentence, re.I):
            continue
        if re.search(
            r"\b(?:benefits?|insurance|privacy|tax|salary)\b", sentence, re.I
        ) and not re.search(r"\b(?:employment|hire|role|position)\b", sentence, re.I):
            continue
        if not re.search(
            r"\b(?:residents?|reside|employment|eligible|ineligible|hiring|hire|work from)\b",
            sentence,
            re.I,
        ):
            continue
        if re.search(
            r"\b(?:may|might|subject to|unless|except|if|provided|conditional|"
            r"but|excluding|not excluded|not ineligible|no longer|only|not all)\b",
            sentence,
            re.I,
        ):
            ambiguous.append(sentence)
        elif re.search(
            r"(?:Washington(?: state)?|WA)\s+(?:residents?\s+)?(?:are\s+)?"
            r"(?:not eligible|ineligible|excluded)|(?:cannot|do not|not able to)"
            r"\s+(?:hire|employ).*\b(?:Washington|WA)\b|(?:excluding|except)"
            r"\s+(?:Washington|WA)\b",
            sentence,
            re.I,
        ):
            negative.append(sentence)
        elif re.search(r"\b(?:not|never|cannot|ineligible|excluded|unavailable)\b", sentence, re.I):
            ambiguous.append(sentence)
        elif re.search(
            r"(?:employment|hiring)\s+(?:is\s+)?(?:available|permitted|open)"
            r".*\b(?:Washington|WA)\b|(?:Washington(?: state)?|WA)\s+residents?"
            r"\s+(?:are\s+)?(?:eligible|can apply)|(?:can|may)\s+(?:hire|employ)"
            r".*\b(?:Washington|WA)\b",
            sentence,
            re.I,
        ):
            positive.append(sentence)
        else:
            ambiguous.append(sentence)
    quotes = tuple(dict.fromkeys((*positive, *negative, *ambiguous)))
    status = "unknown"
    reason = "No unambiguous supported Washington employment statement."
    if complete and not ambiguous and bool(positive) != bool(negative):
        status = "eligible" if positive else "excluded"
        reason = "Explicit description statement; employer confirmation still required."
    elif quotes:
        reason = "Description incomplete, conflicting, conditional, or unsupported statement."
    return WashingtonEligibility(
        status=status,
        reason=reason,  # pyright: ignore[reportArgumentType]
        evidence=tuple(EvidenceField(field="washington_eligibility", quote=q) for q in quotes),
    )


def external_application_url(href: str) -> str | None:
    """Accept an explicit HTTPS destination without DNS resolution or redirects."""
    if len(href) > 2048 or re.search(r"[\s\x00-\x20\x7f\\]", href):
        return None
    try:
        parsed = urlsplit(href)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            return None
        if (
            not host
            or "." not in host
            or host == "linkedin.com"
            or host.endswith(".linkedin.com")
            or host.endswith((".local", ".localhost", ".internal"))
        ):
            return None
        try:
            ipaddress.ip_address(host)
            return None  # Do not expose literal IP destinations, including private networks.
        except ValueError:
            pass
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            return None
        return href
    except ValueError:
        return None


def posting_from_visible(text: str | None, times: tuple[tuple[str, str], ...] = ()) -> JobPosting:
    visible = text or ""
    repost = bool(re.search(r"\breposted\b", visible, re.I))
    posted: set[datetime] = set()
    reposted: set[datetime] = set()
    dates: set[date] = set()
    issues: list[str] = []
    evidence: list[EvidenceField] = []
    attributes: list[JobTimestampAttribute] = []
    explicit_dates = tuple(
        (m.group(), m.group("value"))
        for m in re.finditer(
            r"\b(?:Originally\s+)?(?:Posted|Published)(?: on)?\s+"
            r"(?P<value>\d{4}-\d{2}-\d{2})(?![T\d])",
            visible,
            re.I,
        )
    )
    for label, value in (*times, *explicit_dates):
        if not re.search(r"\b(?:posted|published|reposted|ago)\b", label, re.I):
            continue
        evidence.append(EvidenceField(field="posting", quote=label))
        attributes.append(JobTimestampAttribute(visible_label=label, raw_datetime=value))
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                if not re.search(r"reposted", label, re.I) and (
                    not repost or re.match(r"originally\s+(?:posted|published)\b", label, re.I)
                ):
                    dates.add(date.fromisoformat(value))
                else:
                    issues.append("repost_date_is_not_original_posting_date")
            else:
                instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    issues.append("timestamp_timezone_unspecified")
                    continue
                original_label = bool(
                    re.match(r"(?:originally\s+)?(?:posted|published)\b", label, re.I)
                )
                (
                    reposted
                    if re.search(r"reposted", label, re.I) or (repost and not original_label)
                    else posted
                ).add(instant)
        except ValueError:
            issues.append("invalid_explicit_timestamp")
    if (
        len(posted) > 1
        or len(reposted) > 1
        or len(dates) > 1
        or (posted and dates and next(iter(posted)).date() != next(iter(dates)))
    ):
        issues.append("conflicting_timestamps")
        posted.clear()
        reposted.clear()
        dates.clear()
    if repost or reposted:
        issues.append("repost_is_not_new_job")
    return JobPosting(
        timestamp_attributes=tuple(attributes),
        posted_at=next(iter(posted), None),
        posted_date=next(iter(dates), None),
        reposted_at=next(iter(reposted), None),
        is_repost=True if repost or reposted else None,
        freshness="explicit_timestamp"
        if posted or reposted
        else "explicit_date"
        if dates
        else "relative_only"
        if re.search(r"\bago\b", visible)
        else "unknown",
        uncertainties=tuple(dict.fromkeys(issues)),
        evidence=tuple(evidence),
    )
