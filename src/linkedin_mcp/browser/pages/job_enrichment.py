"""Read visible, job-scoped DOM evidence without activating application controls."""

from __future__ import annotations

import re
from typing import Literal

from playwright.async_api import Locator

from linkedin_mcp.domain.job_metadata import external_application_url, posting_from_visible
from linkedin_mcp.domain.models import EvidenceField, JobPosting


async def visible_posting(root: Locator, text: str) -> JobPosting:
    times: list[tuple[str, str]] = []
    nodes = root.locator("time[datetime]")
    if await nodes.count() > 20:
        return JobPosting(uncertainties=("timestamp_inventory_bound",))
    for index in range(await nodes.count()):
        node = nodes.nth(index)
        if await node.is_visible():
            label = (await node.inner_text()).strip()
            value = await node.get_attribute("datetime")
            if label and value:
                times.append((label, value))
    return posting_from_visible(text, tuple(times))


async def visible_application(
    root: Locator,
) -> tuple[
    str | None,
    Literal["visible_external", "unknown", "ambiguous", "rejected"],
    tuple[EvidenceField, ...],
]:
    links = root.get_by_role(
        "link", name=re.compile(r"^apply(?: on (?:company|employer) website)?$", re.I)
    )
    if await links.count() > 20:
        return None, "ambiguous", ()
    destinations: dict[str, str] = {}
    rejected = False
    for index in range(min(await links.count(), 20)):
        link = links.nth(index)
        if not await link.is_visible():
            continue
        href = await link.get_attribute("href")
        label = (await link.inner_text()).strip()
        if not href or not label:
            continue
        safe = external_application_url(href)
        if safe is None:
            rejected = True
        else:
            destinations[safe] = label
    if len(destinations) == 1 and not rejected:
        href, label = next(iter(destinations.items()))
        return href, "visible_external", (EvidenceField(field="application_url", quote=label),)
    if len(destinations) > 1 or (destinations and rejected):
        return None, "ambiguous", ()
    return None, "rejected" if rejected else "unknown", ()


async def description_state(
    box: Locator,
) -> tuple[str | None, Literal["complete", "incomplete", "unavailable", "unknown"], str]:
    if not await box.count() or not await box.is_visible():
        return None, "unknown", "No visible description container."
    text = (await box.inner_text()).strip()
    if re.fullmatch(r"(?:job )?description (?:is )?(?:unavailable|not available)\.?", text, re.I):
        return None, "unavailable", "The visible UI explicitly reports description unavailable."
    if not text:
        return None, "unknown", "Description is empty or has not rendered."
    busy = await box.locator('[aria-busy="true"], [role="progressbar"]').count()
    busy = busy or await box.get_attribute("aria-busy") == "true"
    clipped = await box.evaluate("""element => {
      const nodes = [element, ...element.querySelectorAll('*')];
      if (nodes.length > 500) return true;
      let parent = element.parentElement;
      for (let i = 0; parent && i < 3; i++, parent = parent.parentElement) nodes.push(parent);
      return nodes.some(node => {
        const style = getComputedStyle(node);
        return node.getAttribute('aria-expanded') === 'false' ||
          (style.webkitLineClamp !== 'none' && Number(style.webkitLineClamp) > 0) ||
          (['hidden', 'clip'].includes(style.overflowY) &&
            node.scrollHeight > node.clientHeight + 1);
      });
    }""")
    controls = box.locator('[data-testid="expandable-text-button"]')
    collapsed = any([await controls.nth(i).is_visible() for i in range(await controls.count())])
    if clipped or collapsed or re.search(r"(?:…|\.\.\.)\s*(?:more)?\s*$", text):
        return text, "incomplete", "Visible clipping or remaining description expansion control."
    if busy:
        return text, "unknown", "Description is still loading."
    await box.page.wait_for_timeout(75)
    if not await box.is_visible():
        return text, "unknown", "Description disappeared during the bounded stability check."
    if (
        await box.get_attribute("aria-busy") == "true"
        or await box.locator('[aria-busy="true"], [role="progressbar"]').count()
    ):
        return text, "unknown", "Description started loading during the bounded stability check."
    if (await box.inner_text()).strip() != text:
        return text, "unknown", "Description changed during the bounded stability check."
    return (
        text,
        "complete",
        "Stable, visible, uncollapsed rendered description; not employer-source completeness.",
    )
