# 08 — The Deployment Module

*Internal onboarding doc — AI Platform, Deployment (`Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`)*

## 1. Executive Summary

Every module up to this one — Gateway, Providers, Runtime, Tools, Memory, Tracing, Evaluation — has been runnable and testable entirely inside one developer's Python environment via `uvicorn --reload` and `pytest`. None of that guarantees the platform runs the same way anywhere else: a teammate's machine might have a different Python patch version, a different set of globally-installed packages, or a `pip install` that resolves slightly different transitive dependency versions.

Deployment is where the platform stops being "code that works on my machine" and becomes a reproducible artifact: a multi-stage `Dockerfile` that builds a slim, non-root runtime image; a `docker-compose.yml` for one-command local runs; and a GitHub Actions CI workflow that runs the entire test suite (now 67+ tests across seven modules) on every push and pull request. This is infrastructure, not a Python package — there's no `ai_platform/deployment/` directory, because packaging a running system isn't a code-level concern the platform's own modules should import or depend on.

## 2. The Problem

Without a deployment story, several real failure modes are still open even though every module's tests pass:

- **"Works on my machine" isn't a guarantee.** A developer's local `.venv` can accumulate a slightly different dependency set than a fresh install would produce, especially without a lockfile — the platform's `pyproject.toml` pins minimum versions (`fastapi>=0.115`), not exact ones.
- **There's no way to run the platform without a Python toolchain already set up.** Anyone who wants to try the Gateway — a teammate, a reviewer, a CI runner — needs Python 3.11+, `pip install -e ".[dev]"`, and the right working directory, every time.
- **Nothing verifies the test suite before code lands.** Every module in this platform has thorough tests, but nothing was actually *running* them automatically on a pull request — a regression could merge to `main` with all the tests it broke sitting unrun.
- **The platform has no packaged, distributable form.** "Deploy this to a server" or "hand this to another team" has no concrete answer without an image or artifact to hand over.

## 3. Motivation

Containerization exists to collapse "works on my machine" into "works," by shipping the runtime environment (base OS packages, Python interpreter, resolved dependencies) alongside the code, rather than assuming the target machine will reconstruct it correctly. A multi-stage build is the standard way to do this without bloating the shipped image: a `builder` stage has whatever's needed to resolve and build the package (`pip`, its own cache, source files), and only the *installed result* is copied into a second, minimal stage — the final image never contains build tooling it doesn't need at runtime.

CI exists for the same reason testing exists at all, applied at the team level rather than the individual level: a test suite that only runs when a developer remembers to run it locally is a test suite that will eventually not run before a bad change merges. Wiring `pytest` into a GitHub Actions workflow makes "did this break anything" a property of the pull request itself, not a step someone has to remember.

## 4. Responsibilities

**Deployment should:**
- Produce a reproducible runtime image via a multi-stage `Dockerfile` (resolve/install in a `builder` stage, run from a slim, non-root final stage)
- Expose a way to run the platform locally with one command (`docker-compose.yml`), environment-driven exactly like local `uvicorn` already is (same `AI_PLATFORM_*` variables `common/config.py` already reads)
- Run the full test suite automatically on every push/PR (`.github/workflows/ci.yml`)
- Provide a container-level health check that exercises the same `/health` route the Gateway already serves, so an orchestrator (Docker, Kubernetes, ECS) has a real liveness signal without new application code

**Deployment should NOT:**
- Introduce new application code or Python packages under `ai_platform/` — it packages what already exists, it doesn't add platform behavior
- Decide *where* the platform runs in production (which cloud, which orchestrator) — a `Dockerfile` and a CI workflow are portable across ECS, Kubernetes, Cloud Run, or a bare VM; committing to one of them is a separate, later decision
- Manage secrets — `AI_PLATFORM_ANTHROPIC_API_KEY` and friends are still expected to come from the environment (a `.env` file locally, a secrets manager in real production), never baked into the image
- Add a shared backend (Redis, Postgres) to `docker-compose.yml` speculatively — `InMemoryStore` and `InMemoryTracer` are still single-process by design (named limitations in the Memory and Tracing tutorials); Compose only grows a second service the moment one of those modules actually gets a distributed backend

## 5. Architecture

```
                    Developer / CI runner
                          │
                          │  docker compose up --build
                          │  (or: docker build && docker run)
                          ▼
      ┌──────────────────────────────────────────┐
      │  Dockerfile — multi-stage build                │
      │                                                  │
      │  ┌──────────────┐      ┌──────────────────┐   │
      │  │  builder          │      │  runtime (final)      │   │
      │  │  python:3.11-slim │ ───► │  python:3.11-slim      │   │
      │  │  pip install .    │ COPY │  + /usr/local (deps)   │   │
      │  │  → /install       │      │  + ai_platform/         │   │
      │  └──────────────┘      │  USER appuser (non-root)│   │
      │                          │  HEALTHCHECK /health      │   │
      │                          │  CMD uvicorn ...           │   │
      │                          └──────────────────┘   │
      └──────────────────────────────────────────┘
                          │
                          ▼
                 container :8000  ──►  same FastAPI app
                                       (create_app() in api/app.py)
                                       unchanged by containerization

      .github/workflows/ci.yml
        on: push (main), pull_request
        → pip install -e ".[dev]"
        → pytest -q     (all 7 modules' tests, every push/PR)
```

Upstream: nothing inside `ai_platform/` depends on Deployment — it's purely how the existing `create_app()` FastAPI application gets packaged and run. Downstream: the `Dockerfile`'s `CMD` invokes the exact same `uvicorn ai_platform.api.app:app` entrypoint the README's local dev instructions already used — containerizing changed *how* the process starts, not *what* starts. Sideways: CI runs the identical `pytest` command a developer runs locally; it doesn't maintain a separate test configuration.

## 6. Request Flow

Two flows worth walking through — building/running the image, and a CI run:

**Building and running:**
1. **`docker build .`** starts the `builder` stage: copies `pyproject.toml` and `ai_platform/` only (not `tests/`, `.venv/`, `.git/` — excluded via `.dockerignore`), then `pip install --prefix=/install .` resolves and installs the package plus its dependencies into an isolated prefix.
2. **The final stage** starts fresh from `python:3.11-slim`, creates a non-root `appuser`, then `COPY --from=builder /install /usr/local` pulls in only the *installed result* — no pip cache, no build artifacts, no compiler toolchain lingering in the shipped layer.
3. **`ai_platform/` source is copied directly** into the final stage too (not reinstalled) — the package's actual Python files need to exist on disk for `uvicorn ai_platform.api.app:app` to import them; installing via `pip install .` in the builder stage already registered the package metadata, and copying the source again keeps the final image self-contained without a second `pip install`.
4. **`USER appuser`** switches off root before the image's `CMD` ever runs — the container process never has more privilege than it needs, standard container hardening.
5. **`docker run` (or `docker compose up`)** starts the container; `uvicorn` binds `0.0.0.0:8000` (not `127.0.0.1` — has to be reachable from outside the container's network namespace) and imports `ai_platform.api.app:app`, running through `create_app()` exactly as local dev does.
6. **Docker's `HEALTHCHECK`** runs `python -c "urllib.request.urlopen('http://localhost:8000/health')"` every 30s — reusing the Gateway's existing `/health` route rather than inventing a separate readiness mechanism, so an orchestrator's liveness probe reflects the same signal a developer would check manually.

**CI:**
1. **A push or pull request against `main`** triggers `.github/workflows/ci.yml`.
2. **`actions/checkout` + `actions/setup-python@v5`** provision a clean Python 3.11 environment — no leftover local state, unlike a developer's long-lived `.venv`.
3. **`pip install -e ".[dev]"`** installs the package plus `pytest`/`pytest-asyncio`/`httpx` from `pyproject.toml`'s `[project.optional-dependencies].dev`.
4. **`pytest -q`** runs the entire suite — Gateway, Providers, Runtime, Tools, Memory, Tracing, Evaluation — and the workflow fails the check on any failure, blocking merge on a broken build rather than relying on someone noticing later.

## 7. Design Decisions

**Why a multi-stage `Dockerfile` instead of a single `FROM python:3.11-slim` stage that just runs `pip install`?**
A single-stage build would ship pip's build machinery, its download cache, and any transient build dependencies in the final image — extra size and extra attack surface for nothing the running application needs. The builder stage exists purely to produce `/install`; the final stage copies only that directory tree via `COPY --from=builder`, so the shipped image is exactly "Python + the platform's resolved dependencies + the platform's source," nothing else.

**Why `python:3.11-slim` instead of a full `python:3.11` image, or a `-alpine` variant?**
`-slim` (Debian-based, minimal packages) is the standard middle ground: meaningfully smaller than the full image without Alpine's musl-libc compatibility risk for compiled Python dependencies (some packages ship glibc-linked wheels that behave differently or fail to install cleanly on Alpine). None of this platform's dependencies need Alpine's extra size savings badly enough to accept that risk.

**Why run as a non-root `appuser` instead of the container's default root user?**
Defense in depth: if a future vulnerability (in this platform's code or a dependency) allowed arbitrary code execution inside the container, running as a non-root user limits what that execution can do to the container's own filesystem — it's a standard, low-cost hardening step with no functional downside for a stateless HTTP service like this Gateway.

**Why does the `HEALTHCHECK` reuse `/health` instead of a container-specific readiness endpoint?**
The Gateway already has a `/health` route (from the very first module) whose entire job is "can this process serve a request" — inventing a second, container-only health mechanism would just be two ways of asking the same question. Reusing it means the health-check story is identical whether you're curling it manually or an orchestrator is polling it every 30 seconds.

**Why is there no `ai_platform/deployment/` Python package, unlike every other module?**
Because Deployment isn't a runtime dependency of the platform — nothing in `RuntimeEngine`, the Gateway, or any other module imports or calls anything related to how the process gets built or started. It's infrastructure *around* the application, not a component *of* it, so it belongs in root-level config files (`Dockerfile`, `docker-compose.yml`, `.github/workflows/`), the same way `pyproject.toml` itself isn't inside `ai_platform/`.

**Why does `docker-compose.yml` not add Redis, Postgres, or any other backing service?**
`InMemoryStore` (Memory) and `InMemoryTracer` (Tracing) are both explicitly named as single-process, v0.1 choices in their own tutorials — adding a shared backend to Compose today would be infrastructure with nothing in the application layer that uses it. Compose should grow a second service the moment one of those modules actually swaps its backend, not speculatively ahead of that change.

## 8. Alternative Designs

| Alternative | Why not |
|---|---|
| **Single-stage `Dockerfile`** | Ships build tooling and cache in the final image for no runtime benefit — see the multi-stage design decision above. |
| **A `Makefile` or shell script wrapping `docker build`/`docker run` instead of `docker-compose.yml`** | Works, but Compose's declarative service definition (ports, environment defaults via `${VAR:-default}`) is the more standard, more discoverable convention for "how do I run this locally," and scales cleanly to a second service the moment one is needed, without inventing new script flags. |
| **Running CI tests inside the Docker image itself (e.g. `docker build --target test`)** | Would couple "does the code pass tests" to "does the container build," making a slow Docker build part of every CI run's critical path. Running `pytest` directly against a fresh `actions/setup-python` environment is faster and tests the same code the image would contain, without needing the image to exist first. |
| **A Kubernetes manifest / Helm chart as part of this module** | Premature — there is no evidence yet this platform runs on Kubernetes specifically (vs. ECS, Cloud Run, or a single VM), and a manifest encodes real assumptions (replica count, resource limits, ingress) this codebase has no basis for yet. The `Dockerfile` is the portable artifact; which orchestrator consumes it is a later, environment-specific decision. |

## 9. Trade-offs

**Gained:** a reproducible, portable runtime artifact (the Docker image) that runs identically on any machine with Docker installed, a documented one-command local run path (`docker compose up --build`), and an automated gate (CI) that actually runs the test suite on every change instead of relying on developer discipline.

**Cost:** a `Dockerfile` and CI workflow are one more thing to keep in sync with the application as it evolves (e.g., a new dependency added to `pyproject.toml` needs no `Dockerfile` change today since it installs via `pip install .`, but a new required environment variable would need a `docker-compose.yml` update to stay discoverable). The image also still assumes single-process, in-memory backends throughout — it packages what the platform is today, not a distributed-deployment topology, which is a deliberately deferred concern (see Production Evolution).

## 10. Production Evolution

```
v0.1 (this module)
  single-service Dockerfile + docker-compose.yml
  CI runs pytest on every push/PR to main
  in-memory backends throughout (Memory, Tracing) — single replica only
        │
        ▼
v0.2
  image published to a registry (GHCR/ECR/Docker Hub) on merge to main,
    tagged by commit SHA — an actual deployable artifact, not just
    locally buildable
  CI also runs linting/type-checking (ruff/mypy) alongside pytest
  docker-compose grows a Redis service once Memory/Tracing need a
    shared backend for more than one replica
        │
        ▼
Enterprise version
  container orchestration (Kubernetes manifests or equivalent):
    replica counts, resource requests/limits, rolling deploys
  secrets management integration (Vault, AWS Secrets Manager) instead
    of plain environment variables for AI_PLATFORM_ANTHROPIC_API_KEY
  staging/production environment separation with promotion gates
    (CI passes → staging deploy → smoke tests → production deploy)
        │
        ▼
Large-scale platform
  multi-region deployment with traffic routing/failover
  blue-green or canary deployment strategy, feeding back into the
    Tracing module's latency/error data to auto-rollback a bad release
  infrastructure-as-code (Terraform/Pulumi) managing the full stack
    the Dockerfile and Compose file only describe the application layer of
```

The scaling challenge here is almost entirely external to the application code itself — the same `Dockerfile` and FastAPI app from v0.1 keep working through most of this evolution; what changes is everything *around* it (registry, secrets, orchestration, multi-region topology), which is exactly why Deployment was built as portable, standalone infrastructure rather than something baked into `ai_platform/`.

## 11. Real-world Examples

- **The Twelve-Factor App** — the foundational methodology behind this module's choices: config via environment variables (already true since `common/config.py`'s first commit), stateless processes, and "build, release, run" as separate stages — exactly what the multi-stage `Dockerfile` and CI workflow implement.
- **GitHub Actions** — the CI platform used here; the same `on: push`/`on: pull_request` trigger pattern and matrix-of-jobs model scales to linting, multi-version testing, and image publishing as the platform grows.
- **Docker's official multi-stage build guide** — the exact pattern this `Dockerfile` follows: a `builder` stage for resolution/compilation, a minimal final stage for runtime.
- **Kubernetes liveness/readiness probes** — the production analog of this module's `HEALTHCHECK`; the same "hit an HTTP endpoint, expect 200" contract this platform's `/health` route already satisfies.

## 12. Common Mistakes

- **Baking secrets (API keys) into the image via `ENV` in the `Dockerfile` or `ARG` at build time.** Anyone with the image has the secret, forever, baked into a layer — secrets belong in runtime environment variables or a secrets manager, never in the image itself, which is why this `Dockerfile` sets no `AI_PLATFORM_ANTHROPIC_API_KEY` default at all.
- **Running the container process as root "because it's simpler."** Costs nothing to avoid (`useradd` + `USER appuser`) and meaningfully reduces what a compromised process can do.
- **Copying the entire build context (`.git/`, `.venv/`, `tests/`) into the image** without a `.dockerignore` — bloats the image and can leak local state (a stray `.env`) into a shipped artifact.
- **Treating "the Dockerfile builds" as equivalent to "the application works."** This module's own verification didn't stop at `docker build` — it ran the container, curled `/health`, sent a real request through the Gateway → Runtime → Provider chain, and checked the Docker-level `HEALTHCHECK` transitioned to `healthy`, because a successful build says nothing about whether the process inside actually serves traffic correctly.
- **Adding infrastructure (Redis, a message queue) to `docker-compose.yml` before any application code actually uses it.** Compose should describe what the application needs today, not a speculative future architecture.

## 13. Best Practices

- Use multi-stage builds to keep build tooling out of the final runtime image.
- Never run a container process as root without a specific reason to.
- Reuse the application's own health endpoint for container health checks instead of inventing a parallel mechanism.
- Keep configuration environment-driven end to end — local dev, Compose, and real production should all read the same variable names, just from different sources.
- Verify a deployment artifact by actually running it (build → run → hit the real endpoints → check health), not just by confirming the build step exits zero.
- Run the full test suite in CI on every push/PR — a test suite that isn't automatically enforced isn't really a gate.

## 14. Interview Knowledge

**Must Know**
- Why multi-stage Docker builds exist and what problem they solve versus a single-stage build.
- The Twelve-Factor App principles most relevant to a containerized service: config via environment, stateless processes, explicit dependency declaration.
- Why running containers as non-root is a standard security practice.

**Good to Know**
- The difference between a liveness check (is the process alive) and a readiness check (can it currently serve traffic) — this module's single `/health` endpoint conflates the two for simplicity, a legitimate v0.1 choice worth naming explicitly.
- Why CI should run the same commands a developer runs locally (`pytest -q`), rather than a parallel, drifting test configuration.
- The role of a container registry (GHCR, ECR, Docker Hub) in going from "an image that builds locally" to "an artifact that can actually be deployed elsewhere" — the concrete v0.2 gap this module leaves open.

**Advanced**
- Blue-green and canary deployment strategies, and how they consume observability data (this platform's Tracing module) to decide whether to roll forward or back automatically.
- Secrets management patterns (Vault, cloud-native secrets managers) versus plain environment variables, and the threat model each addresses.
- Multi-region deployment and data-locality/failover concerns once a platform serves traffic globally rather than from one region.

## 15. Key Takeaways

1. Deployment turns "code that passes tests on a developer's machine" into a reproducible, portable artifact — a multi-stage `Dockerfile`, a one-command local run (`docker-compose.yml`), and an automated test gate (CI) on every push/PR.
2. The image ships only what's needed at runtime — dependencies resolved in a throwaway `builder` stage, copied into a minimal, non-root final stage — never build tooling or secrets.
3. Containerization changes *how* the process starts, not *what* starts — the same `create_app()` FastAPI application and the same `/health` route are reused unchanged, including as the container's own health check.
4. This module is deliberately infrastructure, not a Python package — nothing in `ai_platform/` depends on it, mirroring the Twelve-Factor separation between an application and how it's built/run.
5. A build that succeeds proves nothing about a deployment that works — this module's own verification ran the container, hit `/health` and `/v1/chat` for real, and confirmed the Docker-level health check reported `healthy` before calling the module done.

## Further Reading

1. Docker — [Multi-stage builds](https://docs.docker.com/build/building/multi-stage/) (official docs) — the exact pattern this module's `Dockerfile` implements.
2. The Twelve-Factor App — [https://12factor.net/](https://12factor.net/) — the methodology this platform has followed since `common/config.py`'s first commit, made explicit here at the deployment layer.
3. Docker — [Dockerfile best practices](https://docs.docker.com/build/building/best-practices/) — non-root users, layer caching, `.dockerignore`, and image size guidance.
4. GitHub Actions — [Building and testing Python](https://docs.github.com/en/actions/automating-builds-and-tests/building-and-testing-python) (official docs) — the CI pattern this module's `.github/workflows/ci.yml` follows.
5. Kubernetes — [Liveness, readiness, and startup probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/) — the production evolution of this module's Docker-level `HEALTHCHECK`, relevant reading for the Enterprise-tier orchestration step named in Production Evolution.

## Next Module

This closes the loop the original README started: Gateway → Providers → Runtime → Tools → Memory → Tracing → Evaluation → Deployment is now a complete, tested, containerized, CI-gated platform. From here, further work is evolutionary rather than a new module — pick any Production Evolution section above (a second `ModelProvider`, a Redis-backed `MemoryStore`/`Tracer` for multi-replica deployments, an LLM-as-judge `Grader`, or publishing the image to a registry) and treat it as its own deliberate, evidence-driven pass, the same discipline every module in this platform has followed from the first commit.
