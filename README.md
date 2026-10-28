# Kizuna

Monorepo for the Kizuna rebuild. See `PLAN.md` and `ARCHITECTURE.md` at the repo root for the product and architecture decisions (frozen — PLAN.md section 17). This README covers only running the services locally.

Phase 1 status: each service boots and answers its own health check; the schema v2 migration applies to a fresh Supabase Postgres; `backend-dotnet` verifies Supabase JWTs and bootstraps a `profiles` row on first authenticated call. No internal token to Python, no chat logic yet — that's later phases.

## Layout

```
/frontend            React + Vite + TypeScript
/backend-dotnet      ASP.NET Core (.NET 10) minimal API
/backend-dotnet.Tests  xUnit tests for backend-dotnet (sibling, not nested, so the
                        SDK's default file glob doesn't pull test files into the main build)
/agent-python        FastAPI (Python), run with uvicorn in a .venv
/db                  SQL migrations
```

## Prerequisites

- Node.js 20+ and npm (tested with Node v26.4.0 / npm 11.17.0)
- .NET SDK 10 (tested with 10.0.103)
- Python 3.12+
- A Supabase project (Postgres + pgvector), or any Postgres with the `vector` extension available, for the migration

## frontend

```bash
cd frontend
npm install
cp .env.example .env   # fill in values
npm run dev
```

Runs on http://localhost:5173 and shows "Kizuna frontend running".

## backend-dotnet

```bash
cd backend-dotnet
cp .env.example .env   # fill in values — see below for where to find them
dotnet run
```

Health check (no auth): `curl http://localhost:5241/health` -> `{"status":"ok"}`.

Authenticated identity: `curl http://localhost:5241/me -H "Authorization: Bearer <supabase-access-token>"` -> `{"user_id": "...", "email": "..."}`, and creates a `profiles` row for that user if one doesn't exist yet. Missing/expired/wrong-audience/tampered tokens get 401. See "Getting a test Supabase JWT" below.

Required env vars (`backend-dotnet/.env.example`): `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_JWKS_URL`, `SUPABASE_ISSUER`, `SUPABASE_AUDIENCE`. For a project `<ref>`: `SUPABASE_URL=https://<ref>.supabase.co`, `SUPABASE_JWKS_URL=https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`, `SUPABASE_ISSUER=https://<ref>.supabase.co/auth/v1`, `SUPABASE_AUDIENCE=authenticated`.

Note: the JWKS endpoint only serves keys for projects using Supabase's asymmetric "JWT Signing Keys" (Project Settings -> API -> JWT Keys -> "Migrate JWT secret" if you're still on the legacy shared secret). Without that migration the endpoint returns an empty key set and every token will 401 here, regardless of validity.

### Getting a test Supabase JWT

With "Confirm email" off for your test project (Authentication -> Settings), signup returns a usable access token directly:

```bash
curl -X POST 'https://<ref>.supabase.co/auth/v1/signup' \
  -H "apikey: <anon-key>" -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"correct-horse-battery-staple"}'
```

Or, for a user that already exists:

```bash
curl -X POST 'https://<ref>.supabase.co/auth/v1/token?grant_type=password' \
  -H "apikey: <anon-key>" -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"correct-horse-battery-staple"}'
```

Both return JSON with an `access_token` field — use that as the Bearer token above. The anon key is in Project Settings -> API.

### Tests

```bash
cd backend-dotnet.Tests
dotnet test
```

Auth/401 behavior tests run against a fake JWKS endpoint, no network or database needed. One test (`ProfileUpsertTests`) also needs a local Postgres reachable at `localhost:5432` with no password for the current OS user (same one used to validate the migration in `/db`) — it creates and drops its own scratch database per run.

## agent-python

```bash
cd agent-python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values
uvicorn main:app --reload --port 8001
```

Health check: `curl http://localhost:8001/health` -> `{"status":"ok"}`.

## db

Apply the migration to your Supabase Postgres (or any Postgres with pgvector and a Supabase-compatible `auth` schema):

```bash
psql "$DATABASE_URL" -f db/0001_schema_v2.sql
```

See `db/README.md` for notes on `is_owner`/`plan` enforcement and what's intentionally excluded from this migration.
