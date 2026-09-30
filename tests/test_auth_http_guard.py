import json

import pytest
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from voice_creator_mcp.auth.http_guard import OAuthResourceGuard
from voice_creator_mcp.auth.scopes import ALL_SCOPES, BASELINE_SCOPES, TOOL_SCOPES

RESOURCE = "https://voice.example.com/mcp"
ISSUER = "https://auth.example.com"
PRM_URL = "https://voice.example.com/.well-known/oauth-protected-resource/mcp"

TOKENS = {
    "reader": ["voice:read"],
    "cloner": ["voice:read", "voice:clone"],
    "no-baseline": ["dialogue:generate"],
}


class FakeVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token not in TOKENS:
            return None
        return AccessToken(
            token=token,
            client_id="claude",
            scopes=TOKENS[token],
            subject=f"user-{token}",
            claims={"iss": ISSUER},
        )


async def echo_app(scope, receive, send) -> None:
    access = get_access_token()
    received = b""
    while scope["type"] == "http":
        message = await receive()
        received += message.get("body", b"")
        if not message.get("more_body"):
            break
    body = json.dumps(
        {"subject": access.subject if access else None, "body_bytes": len(received)}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


@pytest.fixture
def client() -> TestClient:
    guard = OAuthResourceGuard(
        echo_app,
        verifier=FakeVerifier(),
        resource_url=RESOURCE,
        authorization_servers=[ISSUER],
        mcp_path="/mcp",
        scopes_supported=ALL_SCOPES,
        baseline_scopes=BASELINE_SCOPES,
        tool_scopes=TOOL_SCOPES,
        resource_name="Google Voice Creator",
    )
    return TestClient(guard, base_url="https://voice.example.com")


def call_tool(client: TestClient, token: str | None, tool: str, *, scheme: str = "Bearer"):
    headers = {"Mcp-Method": "tools/call", "Mcp-Name": tool}
    if token:
        headers["Authorization"] = f"{scheme} {token}"
    return client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1})


@pytest.mark.parametrize(
    "path", ["/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"]
)
def test_serves_protected_resource_metadata(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    metadata = response.json()
    assert metadata["resource"] == RESOURCE
    assert metadata["authorization_servers"] == [ISSUER]
    assert metadata["scopes_supported"] == list(ALL_SCOPES)
    assert metadata["bearer_methods_supported"] == ["header"]
    assert metadata["resource_name"] == "Google Voice Creator"


def test_missing_token_gets_a_discovery_challenge(client: TestClient) -> None:
    response = client.post("/mcp", json={})

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{PRM_URL}"' in challenge
    assert 'scope="voice:read"' in challenge
    assert "error=" not in challenge  # RFC 6750 3.1: no error code without credentials


def test_invalid_token_is_rejected(client: TestClient) -> None:
    response = call_tool(client, "forged", "voice_list_profiles")

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_token_without_baseline_scope_is_forbidden(client: TestClient) -> None:
    response = client.post("/mcp", headers={"Authorization": "Bearer no-baseline"}, json={})

    assert response.status_code == 403
    challenge = response.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="voice:read"' in challenge


def test_tool_needing_more_scope_triggers_step_up(client: TestClient) -> None:
    response = call_tool(client, "reader", "voice_replicate_profile")

    assert response.status_code == 403
    challenge = response.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="voice:clone voice:read"' in challenge
    assert f'resource_metadata="{PRM_URL}"' in challenge


def test_authorised_tool_call_reaches_the_app_with_the_principal(client: TestClient) -> None:
    response = call_tool(client, "cloner", "voice_replicate_profile")

    assert response.status_code == 200
    assert response.json()["subject"] == "user-cloner"


def test_bearer_scheme_is_case_insensitive(client: TestClient) -> None:
    assert call_tool(client, "reader", "voice_list_profiles", scheme="bearer").status_code == 200


def test_unknown_tool_names_are_left_to_the_server(client: TestClient) -> None:
    assert call_tool(client, "reader", "not_a_tool").status_code == 200


def test_other_paths_pass_through_unauthenticated(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["subject"] is None


def test_handshake_clients_without_mcp_method_still_get_step_up(client: TestClient) -> None:
    """Pre-2026 clients send Mcp-Name on tools/call but no Mcp-Method."""
    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer reader", "Mcp-Name": "voice_replicate_profile"},
        json={},
    )

    assert response.status_code == 403
    assert 'scope="voice:clone voice:read"' in response.headers["www-authenticate"]


def test_other_methods_naming_a_tool_are_not_challenged(client: TestClient) -> None:
    response = client.post(
        "/mcp",
        headers={
            "Authorization": "Bearer reader",
            "Mcp-Method": "prompts/get",
            "Mcp-Name": "voice_replicate_profile",
        },
        json={},
    )
    assert response.status_code == 200


def jsonrpc_call(tool: str) -> dict:
    return {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": tool}}


def test_legacy_clients_without_routing_headers_get_step_up_from_the_body(
    client: TestClient,
) -> None:
    """2025-11-25 clients send no Mcp-* routing headers; the guard reads the JSON-RPC body."""
    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer reader"},
        json=jsonrpc_call("voice_replicate_profile"),
    )

    assert response.status_code == 403
    assert 'scope="voice:clone voice:read"' in response.headers["www-authenticate"]


def test_inspected_bodies_are_replayed_to_the_server(client: TestClient) -> None:
    body = json.dumps(jsonrpc_call("voice_list_profiles")).encode()

    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer reader", "Content-Type": "application/json"},
        content=body,
    )

    assert response.status_code == 200
    assert response.json()["body_bytes"] == len(body)


def test_legacy_batches_need_the_scopes_of_every_call(client: TestClient) -> None:
    batch = [jsonrpc_call("voice_list_profiles"), jsonrpc_call("voice_replicate_profile")]
    response = client.post("/mcp", headers={"Authorization": "Bearer reader"}, json=batch)
    assert response.status_code == 403


def test_unparseable_bodies_are_left_to_the_server(client: TestClient) -> None:
    response = client.post("/mcp", headers={"Authorization": "Bearer reader"}, content=b"{not json")
    assert response.status_code == 200
