from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from voice_creator_mcp.auth.principal import Principal, current_principal


def token(**overrides) -> AccessToken:
    fields = {
        "token": "raw-token",
        "client_id": "claude",
        "scopes": ["voice:read", "voice:clone"],
        "subject": "user-123",
        "claims": {"iss": "https://auth.example.com"},
    }
    fields.update(overrides)
    return AccessToken(**fields)


def test_principal_from_access_token() -> None:
    principal = Principal.from_access_token(token())

    assert principal.issuer == "https://auth.example.com"
    assert principal.subject == "user-123"
    assert principal.client_id == "claude"
    assert principal.scopes == frozenset({"voice:read", "voice:clone"})


def test_workspace_id_is_stable_opaque_and_issuer_scoped() -> None:
    first = Principal.from_access_token(token())
    again = Principal.from_access_token(token(token="another-raw-token"))
    other_issuer = Principal.from_access_token(token(claims={"iss": "https://evil.example"}))

    assert first.workspace_id == again.workspace_id
    assert first.workspace_id != other_issuer.workspace_id
    assert len(first.workspace_id) == 32
    assert "user-123" not in first.workspace_id


def test_tokens_without_subject_fall_back_to_the_client() -> None:
    principal = Principal.from_access_token(token(subject=None))
    assert principal.subject == "client:claude"


def test_current_principal_reads_the_request_auth_context() -> None:
    assert current_principal() is None

    reset = auth_context_var.set(AuthenticatedUser(token()))
    try:
        assert current_principal().subject == "user-123"
    finally:
        auth_context_var.reset(reset)
