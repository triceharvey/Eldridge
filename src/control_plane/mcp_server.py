from __future__ import annotations

import json
from hmac import compare_digest
from typing import Any

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from starlette.types import ASGIApp, Receive, Scope, Send

from control_plane.config import Settings, get_settings
from control_plane.domain import ControlPlaneError
from control_plane.runtime import build_runtime
from control_plane.service import ControlPlaneService


class BearerTokenMiddleware:
    """Local development authentication for the Phase 2 MCP boundary."""

    def __init__(self, app: ASGIApp, *, bearer_token: str) -> None:
        if len(bearer_token) < 32:
            raise ValueError("Windsurf MCP bearer token must be at least 32 characters")
        self.app = app
        self.bearer_token = bearer_token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = [
            value for key, value in scope.get("headers", []) if key.lower() == b"authorization"
        ]
        expected = b"Bearer " + self.bearer_token
        if len(headers) != 1 or not compare_digest(headers[0], expected):
            body = json.dumps({"error": "unauthorized"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_windsurf_mcp_server(
    service: ControlPlaneService, *, principal_id: str
) -> MCPServer[None]:
    server: MCPServer[None] = MCPServer(
        "ai-engineering-control-plane",
        version="0.2.0",
        instructions=(
            "Claim only an explicitly assigned implementation task. Work on the exact Git "
            "branch and base revision in the handoff. Heartbeat while working, then return "
            "the immutable branch-head revision and exact changed-file list. MCP evidence "
            "cannot approve, merge, deploy, or skip validation stages."
        ),
    )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    def claim_implementation_task(task_id: str) -> dict[str, Any]:
        """Claim one explicit READY implementation task and receive its bound handoff."""
        try:
            return service.claim_windsurf_task(task_id=task_id, principal_id=principal_id)
        except ControlPlaneError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    def heartbeat_implementation_task(task_id: str, lease_token: str) -> dict[str, Any]:
        """Renew the caller's active implementation lease while interactive work continues."""
        try:
            return service.heartbeat_windsurf_task(
                task_id=task_id,
                lease_token=lease_token,
                principal_id=principal_id,
            )
        except ControlPlaneError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    def submit_implementation_evidence(
        task_id: str,
        lease_token: str,
        handoff_digest: str,
        result_revision: str,
        files_changed: list[str],
        tests_passed: bool,
        test_summary: str,
        tool_activity_summary: str,
    ) -> dict[str, Any]:
        """Submit Git-bound implementation evidence; test claims remain non-authoritative."""
        try:
            return service.submit_windsurf_evidence(
                task_id=task_id,
                lease_token=lease_token,
                principal_id=principal_id,
                handoff_digest=handoff_digest,
                result_revision=result_revision,
                files_changed=tuple(files_changed),
                tests_passed=tests_passed,
                test_summary=test_summary,
                tool_activity_summary=tool_activity_summary,
            )
        except ControlPlaneError as exc:
            raise ToolError(str(exc)) from exc

    return server


def build_windsurf_mcp_app(
    service: ControlPlaneService,
    *,
    bearer_token: str,
    principal_id: str = "windsurf-cascade",
) -> ASGIApp:
    server = build_windsurf_mcp_server(service, principal_id=principal_id)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=262_144,
        host="127.0.0.1",
    )
    return BearerTokenMiddleware(app, bearer_token=bearer_token)


def main() -> None:
    settings: Settings = get_settings()
    if not settings.windsurf_mcp_enabled:
        raise SystemExit("Windsurf MCP is disabled; explicit activation is required")
    if settings.windsurf_mcp_bearer_token is None:
        raise SystemExit("Windsurf MCP bearer token is required")
    runtime = build_runtime(
        settings,
        create_schema=False,
        activate_provider_clients=False,
        activate_github_app_client=False,
        activate_oidc_client=False,
    )
    app = build_windsurf_mcp_app(
        runtime.service,
        bearer_token=settings.windsurf_mcp_bearer_token.get_secret_value(),
        principal_id=settings.windsurf_mcp_principal_id,
    )
    uvicorn.run(
        app,
        host=settings.windsurf_mcp_host,
        port=settings.windsurf_mcp_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
