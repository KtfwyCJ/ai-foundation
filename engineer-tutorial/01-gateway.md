# 01 — The Gateway Module

*Internal onboarding doc — AI Platform, Gateway component (`ai_platform/api/`)*

## 1. Executive Summary

The Gateway is the platform's single external entry point. Every client — internal service, frontend, or third-party integrator — talks to the AI Platform through the Gateway and never directly to Runtime, the Tool Registry, or a model provider.

It is deliberately "dumb" about AI: it doesn't know how to construct a prompt, call Claude or OpenAI, or run an agent loop. What it *does* own is everything that has to happen before a request is allowed to reach that AI logic: who is calling, how often they're allowed to call, whether the request is well-formed, and — on the way back out — turning internal errors into a consistent external contract.

It was built first, before Runtime or the Provider layer, because it's the boundary everything else plugs into. Building it first forced an early decision that shapes the rest of the platform: **Gateway depends on an interface (`RuntimeClient`), never a concrete implementation.**

## 2. The Problem

Any system that exposes AI capability to more than one caller eventually needs answers to:

- Who is allowed to call this, and how do we know they are who they claim?
- How much can they call it? (Cost control — LLM calls are not free.)
- What happens when a request is malformed?
- What does a caller see when something goes wrong internally?

If you skip a Gateway and let clients call Runtime directly, each of those questions gets answered *inside* whatever route or handler happens to receive the request. In practice that means:

- Auth checks copy-pasted into every new endpoint — and inevitably forgotten on one of them.
- No single place to enforce a quota, so a bug or a malicious caller can generate unbounded provider spend.
- Every internal exception type eventually leaks to clients in whatever shape it happened to be raised in, because there's no translation layer.
- Runtime's internal API becomes a de facto public API — you can no longer refactor it without breaking every caller.

None of this is hypothetical; it's the standard failure mode of systems that grow endpoint-by-endpoint without a boundary layer.

## 3. Motivation

Enterprise systems introduce a gateway/BFF (Backend-for-Frontend) layer specifically to separate two different questions:

- **"Should this request proceed at all?"** — identity, quota, shape. Answered by the Gateway.
- **"What does this request mean, and how do we fulfill it?"** — prompt construction, tool use, provider calls. Answered by Runtime.

Once that split exists, both sides get simpler. Runtime never has to think about HTTP, headers, or API keys — it just implements `RuntimeClient.handle_chat(request) -> response`. The Gateway never has to think about how a response is produced — it just calls the interface. This is the same motivation behind the classic 3-tier "edge / application / domain" split, applied to an AI platform.

## 4. Responsibilities

**The Gateway should:**
- Authenticate the caller (`middleware/auth.py::verify_api_key`)
- Enforce a request quota per authenticated caller (`middleware/rate_limit.py`)
- Validate request shape via schemas (`common/schemas.py::ChatRequest`)
- Translate internal, transport-agnostic errors into HTTP responses (`api/errors.py`)
- Route validated requests to Runtime through the `RuntimeClient` interface (`api/routes/chat.py`)

**The Gateway should NOT:**
- Know how a response is generated (no prompt templates, no provider SDK calls — that's Runtime/Provider Abstraction)
- Own conversation memory (that's the future Memory module)
- Make fine-grained authorization decisions like "can this caller use GPT-4 / call the `delete_record` tool" — today it only answers "is this caller who they claim to be." Fine-grained policy is the future Auth/RBAC + Guardrails module's job.
- Retry, plan, or orchestrate multi-step agent behavior — that's Runtime's job.

Keeping this boundary sharp is what lets Runtime, Tool Registry, and Memory be built, tested, and deployed independently later.

## 5. Architecture

```
                 Client
                    │  HTTP + Bearer token
                    ▼
        ┌───────────────────────────┐
        │          Gateway            │
        │  ┌───────────────────────┐  │
        │  │ verify_api_key (auth)  │  │
        │  └───────────┬───────────┘  │
        │  ┌───────────▼───────────┐  │
        │  │ enforce_rate_limit     │  │
        │  └───────────┬───────────┘  │
        │  ┌───────────▼───────────┐  │
        │  │ ChatRequest validation │  │ (pydantic)
        │  └───────────┬───────────┘  │
        │  ┌───────────▼───────────┐  │
        │  │ routes/chat.py handler │  │
        │  └───────────┬───────────┘  │
        └──────────────┼──────────────┘
                        │  RuntimeClient.handle_chat()  (interface)
                        ▼
              ┌─────────────────────┐
              │  runtime/stub.py       │  ← today: EchoRuntimeClient
              │  (real Runtime later)  │
              └─────────────────────┘
```

Upstream: any HTTP client (frontend, service-to-service caller). Downstream: the `RuntimeClient` Protocol defined in `common/interfaces.py` — currently satisfied by a placeholder (`EchoRuntimeClient`), later by the real Runtime module. The Gateway's code does not change when that swap happens; only `api/dependencies.py::get_runtime_client()` does.

## 6. Request Flow

Walking through `POST /v1/chat` end to end:

1. **Client** sends `POST /v1/chat` with `Authorization: Bearer <key>` and a JSON body.
2. **FastAPI dependency resolution** kicks in before the route body runs. `verify_api_key` (`middleware/auth.py`) reads the `Authorization` header, checks the prefix, and checks the key against `Settings.api_key_set`. If missing/invalid → raises `AuthenticationError`.
3. **`enforce_rate_limit`** (`middleware/rate_limit.py`) depends on the *validated* key from step 2 (FastAPI resolves the dependency chain, so auth always runs first) and calls `RateLimiter.check(key)`. Over quota → raises `RateLimitExceededError`.
4. **Body validation** happens via the `ChatRequest` pydantic model (`common/schemas.py`) — FastAPI parses/validates the JSON body against it. Malformed body → automatic `422` via FastAPI's own `RequestValidationError` path.
5. **Route handler** (`routes/chat.py::chat`) receives the validated `ChatRequest` plus an injected `RuntimeClient` from `get_runtime_client()` (`dependencies.py`).
6. **`await runtime.handle_chat(request)`** — today this hits `EchoRuntimeClient`, which echoes the last user message back wrapped in a `ChatResponse`.
7. **Response** is serialized against `ChatResponse` and returned as `200`.
8. **Any `PlatformError` raised at any step** (steps 2, 3, or inside Runtime) is caught by the single exception handler registered in `api/errors.py`, mapped to a status code via `_STATUS_CODES`, and returned as a uniform `{"error": ..., "detail": ...}` JSON body.

## 7. Design Decisions

**Why an interface (`RuntimeClient` Protocol) instead of importing a concrete Runtime class in routes?**
Dependency Inversion. `routes/chat.py` only knows about the `RuntimeClient` Protocol from `common/interfaces.py`. This is what let the entire Gateway be built, run, and tested *before* Runtime exists — swapping the stub for the real thing later means touching one function (`get_runtime_client`), not every route.

**Why FastAPI's `Depends()` for dependency injection instead of a DI framework or global state?**
`Depends()` gives explicit, readable dependencies — you can see exactly what a route needs by reading its signature — and FastAPI resolves them per-request, which composes naturally with auth → rate-limit chaining. A DI container would be overkill at this scale.

**Why `functools.lru_cache` for singletons (`get_settings`, `get_rate_limiter`, `get_runtime_client`) instead of a class-based singleton or app.state?**
It's the simplest tool that gives correct once-per-process singleton behavior in Python, with no extra machinery. The one cost — tests must explicitly `.cache_clear()` between runs — is paid once in `tests/api/conftest.py` rather than everywhere.

**Why is `common/errors.py` transport-agnostic while `api/errors.py` is HTTP-specific?**
So Runtime, the Tool Registry, and Memory (none of which know about HTTP) can raise the exact same `PlatformError` subclasses. Only the Gateway — the one module that speaks HTTP — translates them into status codes. This keeps business-logic exceptions decoupled from any particular transport (HTTP today; could be gRPC or a queue consumer tomorrow using the same exception types).

**Why does rate limiting depend on the *result* of auth (`enforce_rate_limit(api_key: str = Depends(verify_api_key), ...)`) instead of running independently?**
Two reasons: it guarantees ordering (you can't be rate-limited before you're identified), and it guarantees the rate limiter is keyed by verified identity, not something spoofable like a client-supplied header.

## 8. Alternative Designs

| Alternative | Why not (for this scope) |
|---|---|
| **No gateway — clients call Runtime directly** | Cross-cutting concerns (auth, quota, error shape) get duplicated or forgotten per-endpoint. Rejected outright — this is the problem the module exists to solve. |
| **Infra-level gateway** (Envoy, Kong, AWS API Gateway) handling auth/rate-limit, app only does business logic | A legitimate design at real enterprise scale — offloads TLS, coarse rate limiting, and auth to infrastructure. Rejected *for now* because it adds an infra dependency and makes app-specific logic (e.g., per-model quotas, which needs to see the request body) harder to express. Revisit in Production Evolution below. |
| **Starlette middleware instead of `Depends()` chains** | Middleware runs before routing and can't easily be scoped per-route or unit-tested as an isolated function. `Depends()` gives per-route opt-in and each dependency is independently testable. |
| **Service Locator** (a global registry routes pull dependencies from by string key) | Hides what a route actually needs — you'd have to read the registry to know a route depends on a rate limiter. Explicit `Depends()` parameters are self-documenting; rejected for the same reason "magic" globals are generally avoided. |

## 9. Trade-offs

**Gained:** a single, testable control point for identity and quota; Runtime can be developed and swapped in without touching the Gateway; error handling is consistent across every route by construction, not by convention.

**Cost:** an extra layer of indirection even for the simplest request; the in-memory rate limiter (documented explicitly in `rate_limit.py`) doesn't share state across multiple Gateway processes — fine for one instance, wrong the moment you scale horizontally; `lru_cache` singletons are simple but not hot-swappable at runtime without an explicit cache clear, which is a minor but real testing tax (see `conftest.py`).

## 10. Production Evolution

```
v0.1 (this module)
  single-process FastAPI app
  in-memory rate limiter
  static API keys from env config
  one stub downstream (EchoRuntimeClient)
        │
        ▼
v0.2
  real Runtime wired into get_runtime_client()
  Redis-backed distributed rate limiter (INCR + EXPIRE)
  API keys in a secrets manager / DB, hashed at rest
        │
        ▼
Enterprise version
  OAuth2/JWT + RBAC roles (who can use which model/tool)
  per-tenant quotas and budgets
  audit log emitted per request (who, what, cost)
  policy engine (e.g. OPA) for tool/model allowlists
  distributed tracing (OpenTelemetry) propagated into Runtime
        │
        ▼
Large-scale platform
  infra-level gateway (Envoy/Kong: TLS, mTLS, coarse limits)
    in front of this app-level gateway (AI-specific policy)
  multiple Gateway replicas behind a load balancer
  async job queue for long-running agent tasks, not just sync HTTP
  multi-region deployment with per-region quotas
```

The key scaling challenge at each step is state: v0.1's rate limiter and API-key list live in process memory — everything from v0.2 onward is about moving that state somewhere shared (Redis, a DB, a secrets manager) so the Gateway can run as more than one instance.

## 11. Real-world Examples

- **LiteLLM** — its Proxy Server is essentially this exact pattern productionized: a single OpenAI-compatible endpoint in front of 100+ providers, with virtual API keys, per-key budgets, and rate limits. It's the layer client SDKs actually point at, same role as this Gateway.
- **LangGraph** (LangGraph Platform/Server) — exposes a REST/streaming API in front of a graph; the same boundary exists between "external API contract" and "internal orchestration," analogous to Gateway vs. Runtime here.
- **OpenAI Agents SDK** — a client library, not a server, but when teams self-host it behind FastAPI, they commonly build exactly this kind of auth+rate-limit wrapper before calling into the agent loop.
- **Dify** — distinguishes an external "App API" (key-based) from internal workflow execution, mirroring the Gateway/Runtime split.
- **Langfuse** — not a gateway, but its trace-ingestion API sits at a similar edge, receiving structured events from a request pipeline — a preview of where the future Tracing module will hook into this same boundary.

## 12. Common Mistakes

- **Auth checks inside individual route handlers.** Easy to forget on the next new route. This module avoids it by making auth a `Depends()` that every route explicitly opts into (or, better, could be applied at the router level).
- **Coupling the Gateway directly to a concrete Runtime/Provider class.** Makes both sides hard to test in isolation and creates a tangled import graph. Solved here via the `RuntimeClient` Protocol.
- **Rate limiting by IP address.** Behind a load balancer or corporate NAT, many legitimate users share one IP. Should key by authenticated identity — this module keys by `api_key`, not IP.
- **Raising raw `HTTPException` deep inside business logic.** Couples domain code to a specific web framework. Keep exceptions transport-agnostic (`common/errors.py`) and translate only at the edge (`api/errors.py`).
- **Forgetting to reset process-wide singletons between tests.** With `lru_cache`-based singletons, one test's rate-limit state or monkey-patched env var can silently leak into the next test. Handled explicitly via the `clear_caches` fixture in `tests/api/conftest.py`.

## 13. Best Practices

- Keep the exception hierarchy transport-agnostic; translate to HTTP only at the outermost edge.
- Depend on interfaces (Protocols) across module boundaries, never on concrete implementations from another module.
- Make singletons explicit and cache-clearable for tests rather than hidden global state.
- Key rate limits and quotas by authenticated identity, never by raw IP alone.
- Design the external request/response schema to be stable even as internal implementation changes underneath it.

## 14. Knowledge

**Must Know**
- What an API Gateway does: authentication, rate limiting, request routing, response shaping.
- Authentication vs. authorization — the difference, and why this module only does the former.
- Why dependency injection improves testability (swap real deps for fakes without changing the code under test).
- HTTP status code semantics: `401` (who are you) vs `403` (not allowed) vs `429` (too many requests) vs `422` (malformed) vs `503` (downstream unavailable).

**Good to Know**
- Rate limiting algorithms and trade-offs: fixed window (simple, allows bursts at window edges — what this module uses) vs sliding window vs token bucket vs leaky bucket.
- `typing.Protocol` (structural typing, PEP 544) vs `abc.ABC` (nominal typing) for defining interfaces in Python.
- The Dependency Inversion Principle, and how it differs from simple "dependency injection."

**Advanced**
- Distributed rate limiting: Redis `INCR`+`EXPIRE`, or GCRA (as used by Stripe) for smoother limits than fixed-window.
- Multi-tenant quota systems — per-org and per-user limits composed together.
- Gateway vs. service mesh boundary decisions (in-app gateway vs. Envoy/Istio sidecar) and when to use each.
- Designing idempotent APIs so clients can safely retry through a gateway.
- Streaming LLM responses (SSE/WebSocket) through a synchronous-request-shaped gateway.

## 15. Key Takeaways

1. The Gateway is the platform's single external boundary — cross-cutting concerns (auth, quota, error shape) live here, not scattered across business logic.
2. It depends on an interface (`RuntimeClient`), never a concrete Runtime — Dependency Inversion in practice, and the reason Gateway could be built and tested before Runtime exists.
3. Errors are raised as transport-agnostic exceptions (`common/errors.py`) and translated to HTTP only at the edge (`api/errors.py`), so the same exceptions will work unchanged from Runtime and Tools later.
4. Auth and rate limiting are chained by verified identity, not IP — the only key that's actually meaningful for quota enforcement.
5. In-memory singletons (`lru_cache`) are a deliberate v0.1 simplification, explicitly documented as needing to move to shared state (Redis) the moment there's more than one Gateway replica.

## Further Reading

1. FastAPI — [Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/) (official docs) — the DI pattern used throughout this module.
2. LiteLLM — [Proxy Server docs](https://docs.litellm.ai/docs/proxy/quick_start) — the closest real-world analog to this Gateway.
3. PEP 544 — [Protocols: Structural subtyping](https://peps.python.org/pep-0544/) — the basis for `RuntimeClient`.
4. Stripe Engineering Blog — rate limiting design (token bucket / GCRA at scale) — reference for the v0.2 Redis-backed limiter.
5. Kleppmann, *Designing Data-Intensive Applications* — background on distributed state, relevant once rate limiting/auth state moves out of process memory.

## Next Module

**Provider Abstraction** (`ai_platform/providers/`). The Gateway currently calls a stub (`EchoRuntimeClient`) — to make the platform "real," the next dependency in the chain is a unified interface over LLM providers (Claude, OpenAI, ...), which Runtime will compose against. Building Provider Abstraction next means Runtime — the module after that — can be written against a real, working provider layer instead of another stub.
