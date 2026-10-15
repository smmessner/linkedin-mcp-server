"""Server restrictions tested with synthetic providers and the official MCP client."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import TextContent
from pydantic import AnyUrl, ValidationError

from linkedin_mcp.capabilities import create_default_registry
from linkedin_mcp.config import Settings, runtime_configuration_fingerprint
from linkedin_mcp.container import create_production_container
from linkedin_mcp.domain.models import StrictModel
from linkedin_mcp.errors import AccessDeniedError
from linkedin_mcp.policy.access import AccessPolicy
from tests.contract.test_mcp_protocol import protocol_container, protocol_session

APPROVED = {
    "linkedin.jobs.search",
    "linkedin.jobs.get",
    "linkedin.server.status",
    "linkedin.session.status",
    "linkedin.capabilities.list",
}
# Valid arguments are important: rejection must not merely be a validation failure.
DENIED: tuple[tuple[str, str, dict[str, object]], ...] = (
    ("people.search", "search_people", {"query": "fixture"}),
    ("people.get", "get_person", {"profile_slug": "fixture-person"}),
    ("companies.search", "search_companies", {"query": "fixture"}),
    ("companies.get", "get_company", {"company_slug": "fixture-company"}),
    ("posts.search", "search_posts", {"query": "fixture"}),
    ("posts.get", "get_post", {"post_ref": "activity:7312345678901234567"}),
    ("posts.comments.list", "list_post_comments", {"post_ref": "activity:7312345678901234567"}),
    ("connections.list", "list_connections", {}),
    ("connections.search", "search_connections", {"query": "fixture"}),
    ("invitations.list", "list_invitations", {}),
    ("invitations.send", "send_invitation", {"profile_slug": "fixture-person"}),
    ("invitations.accept", "accept_invitation", {"profile_slug": "fixture-person"}),
    ("invitations.ignore", "ignore_invitation", {"profile_slug": "fixture-person"}),
    ("messaging.search", "search_messages", {"query": "fixture"}),
    ("messaging.conversation.get", "get_conversation", {"conversation_id": "fixture-thread"}),
    ("messaging.send", "send_message", {"conversation_id": "fixture-thread", "message": "Hi"}),
    ("posts.create", "create_post", {"content": {"mode": "text", "text": "Fixture"}}),
    (
        "posts.comment",
        "comment_on_post",
        {"post_ref": "activity:7312345678901234567", "text": "Hi"},
    ),
    (
        "posts.react",
        "react_to_post",
        {"post_ref": "activity:7312345678901234567", "desired_reaction": "like"},
    ),
)


def test_access_mode_is_explicit_and_part_of_runtime_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINKEDIN_MCP_ACCESS_MODE", "job-search-only")
    restricted = Settings()
    assert restricted.access_mode == "job-search-only"
    assert restricted.auto_login_on_start is False
    normal = Settings(access_mode="standard")
    assert runtime_configuration_fingerprint(restricted) != runtime_configuration_fingerprint(
        normal
    )
    for invalid in ("", "true", "job-search", "JOB-SEARCH-ONLY"):
        monkeypatch.setenv("LINKEDIN_MCP_ACCESS_MODE", invalid)
        with pytest.raises(ValidationError):
            Settings()
    with pytest.raises(ValueError, match="Unknown access mode"):
        AccessPolicy(cast("str", "invalid"))  # pyright: ignore[reportArgumentType]
    assert AccessPolicy("job-search-only").allows("linkedin.future.read") is False
    assert AccessPolicy("standard").allows("linkedin.future.read") is True


async def test_exact_mcp_allowlist_and_executable_approved_tools(tmp_path: Path) -> None:
    async with protocol_session(tmp_path, access_mode="job-search-only") as session:
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == APPROVED
        assert all(tool.annotations and tool.annotations.readOnlyHint for tool in tools)
        for name, args in (
            ("linkedin.server.status", {}),
            ("linkedin.session.status", {}),
            ("linkedin.capabilities.list", {}),
            ("linkedin.jobs.search", {"query": "fixture"}),
            ("linkedin.jobs.get", {"job_id": "4100000001"}),
        ):
            result = await session.call_tool(
                name,
                {"context_id": "fixture", "request_id": name, **args}
                if name.startswith("linkedin.jobs.")
                else args,
            )
            assert not result.isError, name
            assert result.structuredContent is not None
            if name == "linkedin.capabilities.list":
                assert {item["name"] for item in result.structuredContent["capabilities"]} == {
                    "linkedin.jobs.search",
                    "linkedin.jobs.get",
                }
            if name == "linkedin.server.status":
                assert result.structuredContent["access_mode"] == "job-search-only"
        assert (await session.list_resources()).resources == []
        assert (await session.list_resource_templates()).resourceTemplates == []
        with pytest.raises(McpError):
            await session.read_resource(AnyUrl("linkedin://sources/fixture-source"))


async def test_direct_mcp_calls_cannot_invoke_hidden_or_unknown_tools(tmp_path: Path) -> None:
    assert {"linkedin." + name for name, _, _ in DENIED} == {
        d.name.value for d in create_default_registry().list()
    } - {"linkedin.jobs.search", "linkedin.jobs.get"}
    async with protocol_session(tmp_path, access_mode="job-search-only") as session:
        for name, _, args in (*DENIED, ("future.read", "unused", {})):
            result = await session.call_tool(
                "linkedin." + name,
                {"context_id": "fixture", "request_id": "denied", **args},
            )
            assert result.isError, name
            assert any(
                isinstance(c, TextContent) and "Unknown tool" in c.text for c in result.content
            )


@pytest.mark.parametrize(("name", "method", "args"), DENIED)
async def test_worker_and_executor_deny_before_queue_replay_or_provider_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, method: str, args: dict[str, object]
) -> None:
    # The fixture intentionally supplies a full registry; policy must still deny.
    container = protocol_container(tmp_path, access_mode="job-search-only")
    descriptor = next(d for d in container.registry.list() if d.name.value == "linkedin." + name)
    request = descriptor.input_model.model_validate(
        {"context_id": "fixture", "request_id": "denied", **args}
    )
    begin = AsyncMock(side_effect=AssertionError("Denied call reached persistence"))
    monkeypatch.setattr(container.repository, "begin_call", begin)
    try:
        for target in (container.worker, container.executor):
            invoke = cast(Callable[[StrictModel], Awaitable[object]], getattr(target, method))
            with pytest.raises(AccessDeniedError):
                await invoke(request)
        await container.worker.start()
        invoke = cast(Callable[[StrictModel], Awaitable[object]], getattr(container.worker, method))
        with pytest.raises(AccessDeniedError):
            await invoke(request)
        assert container.worker.queue_depth == 0
        begin.assert_not_awaited()
    finally:
        await container.close()


async def test_production_composition_contains_only_job_capabilities(tmp_path: Path) -> None:
    container = create_production_container(
        Settings(
            access_mode="job-search-only",
            browser_auto_install=False,
            browser_profile_path=tmp_path / "profile",
            runtime_lock_path=tmp_path / "runtime.lock",
        )
    )
    try:
        assert {d.name.value for d in container.registry.list()} == {
            "linkedin.jobs.search",
            "linkedin.jobs.get",
        }
    finally:
        await container.close()
