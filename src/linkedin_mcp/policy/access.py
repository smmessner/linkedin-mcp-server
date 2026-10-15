"""Immutable, explicit allowlist for the optional job-discovery server mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from linkedin_mcp.domain.models import CapabilityName
from linkedin_mcp.errors import AccessDeniedError

AccessMode = Literal["standard", "job-search-only"]
JOB_CAPABILITIES = frozenset({CapabilityName.JOBS_SEARCH, CapabilityName.JOBS_GET})
JOB_TOOLS = frozenset(
    {
        *(name.value for name in JOB_CAPABILITIES),
        "linkedin.server.status",
        "linkedin.session.status",
        "linkedin.capabilities.list",
    }
)


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    mode: AccessMode = "standard"

    def __post_init__(self) -> None:
        if self.mode not in {"standard", "job-search-only"}:
            raise ValueError("Unknown access mode")

    def allows(self, name: str) -> bool:
        return self.mode == "standard" or name in JOB_TOOLS

    def require(self, name: str) -> None:
        if not self.allows(name):
            raise AccessDeniedError("The capability is unavailable in job-search-only mode.")
