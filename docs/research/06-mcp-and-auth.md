# 06: MCP and authentication best practices for this server

Research date: **2026-09-30**. The MCP spec site (`modelcontextprotocol.io`) and the
OWASP cheat sheets were blocked from the build environment. Evidence therefore comes
from the **MCP Python SDK 2.2.0 source** [SDK], which implements protocol
**2026-07-28**, and from search summaries of the spec and practitioner write-ups
[Docs]/[3P]. The same caveats as [README.md](README.md) apply.

## 1. What the current MCP spec (2026-07-28) asks of a server

**Transport and protocol**

- **The protocol core is stateless.** The `initialize` handshake is removed (SEP-2575),
  and so are `Mcp-Session-Id` and protocol-level sessions (SEP-2567). Version and client
  capabilities travel in `_meta` on every request [3P]. The SDK still accepts the
  2025-11-25 handshake for older clients [SDK].
- **Routing headers.** Streamable HTTP requests carry `Mcp-Method` and `Mcp-Name`
  (SEP-2243), so gateways and rate limiters can act without reading the body [3P].
  The SDK rejects requests whose headers disagree with the body [SDK].
- **Elicitation moved.** Server-initiated elicitation and sampling moved to
  `InputRequiredResult` (multi-round-trip requests) [3P + SDK].
- **Logging is deprecated.** The logging capability is deprecated (SEP-2577), so log to
  stderr instead [SDK].

**Authorization (HTTP only)** [Docs + 3P]

- The MCP server is an **OAuth 2.1 resource server**.
- It **MUST publish RFC 9728 Protected Resource Metadata**, and point to it from
  `WWW-Authenticate: Bearer resource_metadata="…"`.
- It **MUST reject any token not issued for itself**. Clients send the RFC 8707
  `resource` parameter, and the server checks the audience.
- **Token passthrough is forbidden.** Never forward the caller's token to upstream APIs.
- **Insufficient scope returns `403`** with `error="insufficient_scope"` and a `scope`
  hint. The client re-authorizes with the union of its old and new scopes (step-up).
- Client registration: **Client ID Metadata Documents**. Dynamic Client Registration
  is deprecated. This is a client and authorization-server concern.

**Local servers (stdio)** take credentials from the environment. OAuth doesn't apply.

**Security guidance** [3P: OWASP MCP cheat sheet, CoSAI, Wiz]:

- least-privilege scopes
- per-action authorization (against confused-deputy abuse by prompt-injected tool calls)
- input validation and path confinement
- no SSRF surfaces
- DNS-rebinding protection and loopback binding for local HTTP
- audit logs without sensitive payloads
- rate limits
- secrets kept out of code and logs

**Tool design** (MCP builder guidance): service-prefixed snake_case names; accurate
annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`);
typed `outputSchema` plus `structuredContent`; bounded inputs; pagination;
actionable errors; progress for long operations.

## 2. Threat model for a voice-cloning MCP server

| Asset | Why it matters |
|---|---|
| Voice samples and consent recordings | Biometric data (GDPR Art. 9, BIPA and similar) |
| Replicated voice IDs and keys | Let anyone holding them speak as the person |
| Generated audio | Deepfake and fraud potential |
| Gemini credential and quota | Cost, and account abuse |

| Threat | Example |
|---|---|
| Confused deputy / prompt injection | A document read by the agent asks it to clone a voice, or to read `/etc/passwd` via `sample_path` |
| Cross-tenant access | User B lists or plays user A's voice |
| Token misuse | A token for another API replayed here, or this server's token forwarded upstream |
| Cost abuse | Scripted loops of TTS or Live calls |
| Local HTTP attacks | DNS rebinding from a browser page to `127.0.0.1` |
| Credential leaks | Keys in logs, repr, git or tool output; legacy Gemini keys |

## 3. Decisions and where they live

| Practice | Source | Implementation |
|---|---|---|
| stdio uses environment credentials, no OAuth | Spec | `config.Settings` (`GEMINI_API_KEY`, `GEMINI_API_KEY_FILE`) |
| HTTP is an OAuth 2.1 resource server | Spec | `auth/http_guard.py` |
| RFC 9728 metadata, path-specific and root, with CORS | Spec | `OAuthResourceGuard._send_metadata` |
| `401` + `resource_metadata` + baseline `scope`; no `error` without credentials (RFC 6750 §3.1) | Spec / RFC | `OAuthResourceGuard._challenge` |
| Audience-bound JWTs: `iss`, `aud`, `exp`, `nbf`, `sub`; asymmetric-only algorithm allow-list (blocks `none` and HS256 key confusion); fail closed | Spec, RFC 9068 | `auth/jwt_verifier.py` |
| JWKS discovery via RFC 8414 then OIDC, with issuer mix-up check | RFC 8414 | `discover_jwks_uri` |
| Least-privilege scopes grouped by risk; unknown tools denied | Security | `auth/scopes.py` |
| Step-up `403 insufficient_scope` per tool, from `Mcp-Method`/`Mcp-Name`, with a JSON-RPC body fallback for 2025-11-25 clients | Spec | `OAuthResourceGuard._tool_scopes_for` / `_scopes_in_body` |
| Defense in depth: every tool re-checks its scopes from the validated token | Security | `server/guard.py` (`ToolGuard._check_scopes`) |
| No token passthrough: Gemini uses the server's own credential | Spec | `server/composition.py`, `app/backend.py` |
| A private workspace per principal (`sha256(iss, sub)`), `0700`/`0600` | Privacy | `app/workspace.py`, `voices/profiles.py` |
| Remote servers never read server paths (base64 only) and never return them | Security | `app/inputs.py` (`PathPolicy`), `ServerOptions.redact_paths` |
| Local path confinement (optional) that resolves symlinks before checking | Security | `VOICE_CREATOR_ALLOWED_DIRS` |
| Size limits on every input: audio 4 MiB, scripts 20k characters, 20 prompts, request body 12 MiB | Security | tool `Field` constraints, `MAX_REQUEST_BODY_BYTES` |
| Per-principal rate limit on Gemini-costly tools (30/min remote by default) | Security | `app/rate_limit.py` |
| JSON audit trail (tool, opaque workspace, client, outcome, duration; never content) | Security | `app/audit.py` |
| Secure by default: loopback bind; OAuth required; `AUTH=none` only on loopback; HTTPS required off loopback | Security | `auth/config.py` |
| DNS-rebinding protection with Host/Origin allow-lists | Spec | `HttpConfig.transport_security()` |
| Stateless Streamable HTTP (scales horizontally) | Spec 2026-07-28 | `server/http.py` |
| Logs to stderr only, since stdout is the stdio channel | MCP | `__main__.py` |
| `voice_`-prefixed tools, titles, annotations, `outputSchema`, progress, resources for renders | MCP builder | `server/*.py` |
| Legacy Gemini standard keys flagged; key-file permissions checked | Google | `Settings.credential_warnings()` |

## 4. Scopes

| Scope | Tools | Risk |
|---|---|---|
| `voice:read` (baseline) | `voice_validate_recording`, `voice_list_profiles`, `voice_list_gemini_voices`, `voice_preview_script`, render resources | Read your own data |
| `voice:write` | `voice_create_profile`, `voice_design_gemini_voice` | Stores biometric recordings, or creates a designed voice |
| `voice:clone` | `voice_replicate_profile` | **Creates a clone at Google** |
| `voice:delete` | `voice_delete_profile`, `voice_delete_gemini_voice` | Irreversible |
| `dialogue:generate` | `voice_render_dialogue`, `voice_live_dialogue` | Spends quota; speaks as the person |

A typical client first gets `voice:read`. It is challenged for more only when it calls
a tool that needs it, and the user sees each consent screen at that point.

## 5. Google-side credentials

- **Standard keys are gone.** Google rejected unrestricted standard keys (`AIza…`) from
  2026-06-19 and all standard keys from September 2026. Use **auth keys** (`AQ.…`),
  which are bound to a service account with IAM [3P: DoiT, Cybernews]. The server logs
  a warning when it sees an `AIza…` key.
- **Storage.** Keep keys in a secret store: Google Secret Manager, or Docker/Kubernetes
  secrets mounted as `GEMINI_API_KEY_FILE` with mode `600`. Restrict keys to the Gemini
  API and rotate them about every 90 days [Docs].
- **Ephemeral tokens** (`client.auth_tokens.create`, by default 1 min to start a session
  and 30 min of use) only matter when a browser or mobile app talks to Live directly
  [Docs]. This server keeps the key server-side and proxies instead.
- **Enterprise Agent Platform (Vertex).** It uses service accounts or ADC, but rejects
  `consent_audio` and `voice_consent_signature` in Live [SDK]. The inline-voice flow here
  therefore needs the Gemini Developer API.

## 6. What was deliberately not built (and why)

- **An authorization server.** Delegating to an IdP (Auth0, Okta, Entra ID, Keycloak,
  WorkOS, …) is safer than running one. The SDK's AS provider stays unused.
- **Elicitation-based confirmations.** 2026-07-28 moved elicitation to
  `InputRequiredResult`, and client support is new. Consent for risky actions comes
  from scopes (`voice:clone`, `voice:delete`), destructive annotations and the client's
  approval prompts. Revisit once clients support multi-round-trip requests.
- **Token exchange to Google (RFC 8693).** Gemini is called with a server credential,
  so there is no user-delegated Google token to exchange.
- **Opaque-token introspection (RFC 7662), DPoP and mTLS.** These plug into the
  `TokenVerifier` protocol when an IdP needs them.
- **A distributed rate limiter.** The limiter is per process. Multi-instance
  deployments should also rate-limit at the gateway on `Mcp-Method`/`Mcp-Name`.

## 7. Verification

- **Unit tests** cover the auth, guard, config, workspace, input, rate-limit, audit and
  server policies (see `tests/test_auth_*.py`, `test_app_*.py`, `test_server.py`,
  `test_http_app.py`).
- **End-to-end run.** Real uvicorn, RS256 JWTs, and the MCP SDK's streamable-HTTP client
  on both the 2025-11-25 handshake and 2026-07-28 `server/discover`. Observed:

| Check | Result |
|---|---|
| Metadata | 200 |
| No token | `401` with `resource_metadata` and `scope="voice:read"` |
| Wrong audience | `401 invalid_token` |
| Foreign Host header | `421` |
| Missing `voice:write` | `403` step-up on both protocol versions |
| Path input on the remote server | Refused |
| Cross-user listing | Empty |
| Tools list | All 11 tools carry `outputSchema` |

## Sources

- [MCP Authorization, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [WorkOS: the July 28 spec and agent authentication](https://workos.com/blog/mcp-2026-spec-agent-authentication)
- [SSOJet: what the 2026-07-28 authorization spec requires](https://ssojet.com/blog/mcp-authentication-oauth-tokens-security-ai-connections)
- [Descope: the July 2026 MCP revision and identity](https://www.descope.com/blog/post/july-2026-mcp-revision)
- [The 2026-07-28 MCP spec: a server readiness checklist](https://dev.to/gustavo_gated/the-2026-07-28-mcp-spec-a-server-readiness-checklist-14nf)
- [MCP goes stateless: 2026-07-28 migration guide](https://luismori.dev/article/mcp-goes-stateless-2026-07-28-migration-guide/)
- [Developers Digest: 2026-07-28 breaking changes](https://www.developersdigest.tech/blog/mcp-2026-07-28-breaking-changes)
- [MCP authorization scope is the hole the new spec handed you](https://www.rockcybermusings.com/p/mcp-authorization-scope-spec-gap)
- [OWASP MCP Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)
- [Coalition for Secure AI: practical guide to MCP security](https://www.coalitionforsecureai.org/securing-the-ai-agent-revolution-a-practical-guide-to-mcp-security/)
- [Wiz: MCP security](https://www.wiz.io/academy/ai-security/model-context-protocol-security)
- [Gemini API: ephemeral tokens](https://ai.google.dev/gemini-api/docs/ephemeral-tokens)
- [Gemini API: using API keys](https://ai.google.dev/gemini-api/docs/api-key)
- [DoiT: Gemini API key deprecation deadlines](https://www.doit.com/blog/the-gemini-api-key-abuse-fix-is-here-and-it-has-a-deadline)
- [Cybernews: Google ends unrestricted standard keys](https://cybernews.com/security/google-gemini-reject-unrestricted-standard-keys/)
- [Truffle Security: Gemini API key privilege escalation fix](https://trufflesecurity.com/blog/google-fixes-the-gemini-api-key-privilege-escalation-issue)
- RFC 6750 (Bearer), RFC 8414 (AS metadata), RFC 8707 (resource indicators),
  RFC 9068 (JWT access tokens), RFC 9728 (protected resource metadata)
