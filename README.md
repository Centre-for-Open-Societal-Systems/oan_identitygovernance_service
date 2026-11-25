# oan_identitygovernance_service (central-rbac)

FastAPI control plane that onboards applications, roles and users across Keycloak and managed services.
Design: `docs/central-rbac-system-design.md`. Earlier Frappe-based sections of `PLAN.md` are superseded.

## Run

    uv venv && uv pip install -e .
    cp .env.example .env   # set RBAC_DB_* and RBAC_KEYCLOAK_* values
    uv run python -m app.main run      # or: uv run uvicorn app.main:app --reload

Built on `openg2p-fastapi-common` (app setup, config, JSON logging, DB engine, base models, `/ping` health).
Settings use the `RBAC_` prefix (`.env.example`). Tables are created on startup and by `python -m app.main migrate`
(Postgres only). Alembic comes next.

## Login

Direct (A2C-style, backend-for-frontend): `central-rbac` is a confidential client in realm `master`.
Register `${RBAC_PUBLIC_URL}/auth/callback` as its redirect URI. Flow: browser -> `GET /auth/login` -> Keycloak ->
`GET /auth/callback` (backend exchanges the code with the client secret, PKCE) -> httpOnly cookies
`rbac_access` / `rbac_refresh` -> redirect to `RBAC_POST_LOGIN_URL`. Also `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`.

Accepted credentials per call: `Authorization: Bearer`, the `rbac_access` cookie, or (when `RBAC_IAM_ISSUER` is set)
the staff IAM access-token cookie. Cookie-authenticated writes must send `X-Requested-With: central-rbac` and an
`Origin` in `RBAC_CORS_ALLOW_ORIGINS`. Cookie name and role claim shape for the IAM route are unverified.

## Operator roles (client roles on `central-rbac`)

`rbac-admin` (everything), `rbac-operator` (onboard apps, roles, users), `rbac-viewer` (read).

## Implemented (design section)

- Realms, applications (3.1: row, Keycloak client, secret returned once, `OAN Administrator` granted to the operator)
- Roles (3.2): listed live from the application's Keycloak client; created and deleted through the service admin API
  with the operator's token, the service writing the Keycloak client role. Nothing about a role is stored here.
- Permissions with allow-list (3.3), read and written live in the service
- Users create / bulk / enable / disable / grant / revoke (4), roles named by `{application_id, name}`
- Audit log on every write

## Not yet

Drift report (6), service-to-RBAC role reports (3.4), Alembic.

## Deploy

Helm chart: `helm/oan-identitygovernance-service`; dev values: `helm/environments/dev/`.
