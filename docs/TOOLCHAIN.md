# Toolchain, locked dependencies and commands (INV-0102)

Resolved and locked 2026-09-28 on backup-host. Refresh only deliberately: update the lock, re-run `scripts/check.sh --with-images`, record the new versions here and in the next release record.

## Runtimes and base images

| Component | Version | Pin |
|---|---|---|
| Backend image base | Python 3.12.14 (`python:3.12-slim-bookworm`) | `sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e` |
| Host dev interpreter (uv-managed) | CPython 3.12.12 | `backend/.python-version` = 3.12 |
| UI build image | Node 20.20.2 (`node:20-bookworm-slim`) | `sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0` |
| UI serve image | nginx 1.30.5 (`nginxinc/nginx-unprivileged:stable-alpine`) | `sha256:ed04ec1ff34502c339ee5c3ae3f855442398edc1d05591e2b98981dcbbd20b1e` |
| Disposable test database | PostgreSQL 15.19 (`postgres:15-alpine`) | `sha256:f7d23353e1b15400d22ebe31189f4d314b87a4c129cc400c8c2d8d4ca127bf81` |
| Host tools | uv 0.9.18 (also pinned in the backend image), Node 20.20.2, npm 10.8.2, Docker 29.7.2, Compose v5.5.0, host Python 3.9.25 (stdlib scripts only) | — |

## Backend (`backend/uv.lock`, 45 packages)

Runtime: fastapi 0.141.1, starlette 1.7.0, uvicorn 0.54.0 (+uvloop, httptools, watchfiles, websockets), sqlalchemy 2.1.1, psycopg 3.3.6 (binary), alembic 1.20.0, pydantic 2.13.5, pydantic-settings 2.15.0, prometheus-client (locked).
Dev: pytest 9.1.1, httpx 0.28.1, ruff 0.16.9, mypy 2.3.1, pyyaml, types-pyyaml.

## Frontend (`frontend/package-lock.json`, exact versions in `package.json`)

react / react-dom 19.3.0; vite 8.3.1, @vitejs/plugin-react 6.1.1, typescript 6.0.3, vitest 4.1.11, jsdom 29.1.1, @testing-library/react 16.3.3, @testing-library/dom 10.4.2, eslint 10.11.0, @eslint/js 10.0.1, typescript-eslint 8.71.0, eslint-plugin-react-hooks 7.1.1, globals 17.12.0, @types/react(-dom) 19.3.0, @types/node 20.x.

## Commands

| Purpose | Command (from `.`) |
|---|---|
| Everything (the gate) | `scripts/check.sh` (add `--with-images` to build and smoke-test images; `--log docs/evidence/<dir>` to keep output) |
| Backend lint / format / types / unit | `cd backend && uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -m "not integration"` |
| Backend integration (ephemeral DB) | `scripts/test_backend.sh` |
| Frontend | `cd frontend && npm ci && npm run typecheck && npm run lint && npm test && npm run build` |
| Participant bundle leakage | `python3 scripts/check_bundle_leakage.py frontend/dist` |
| Repository contracts | `uv run --project backend --frozen pytest tests/contracts` |
| Source bundle checksum | `python3 scripts/source_bundle.py` |
| Build images (stamped) | `scripts/build_images.sh` |
| Image smoke test (disposable) | `scripts/smoke_stack.sh` |
| Port preflight before first start | `scripts/preflight_ports.sh` |
| Start services (not yet authorized; ED-09) | `scripts/preflight_ports.sh && scripts/build_images.sh && docker compose up -d` |
| Content check (bind 4 profiles x 2 arms, publication, diffs, generated docs) | `cd backend && uv run python -m app.domain.manifests.cli check` |
| Regenerate content schemas / timing docs | `cd backend && uv run python -m app.domain.manifests.cli schemas --write` / `docs --write` |
| Publish new manifest versions (add-only) | `cd backend && uv run python -m app.domain.manifests.cli publish --date YYYY-MM-DD` |
| Reference fixtures | `python3 scripts/extract_demo_reference.py [--check]` |
| Requirement trace | `python3 scripts/build_requirement_trace.py [--check]` |
| Status ledger | `python3 scripts/build_status_ledger.py --check` |

Determinism: installs come only from the two lock files (`uv sync --frozen`, `npm ci`); tests use a virtual clock, provider doubles, and an ephemeral database; no test contacts a real provider or a live database.
