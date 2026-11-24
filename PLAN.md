# Central RBAC: one platform to onboard users in bulk across Keycloak and every app's admin API

Status: proposal. Based on a read of `openg2p-workspace`, `frappe-bench/apps/oan_*` and `oan_da_app_services` (2026-11-18).

## 1. What exists today (verified in code)

| Layer | Where | What it gives us | Gap for central RBAC |
|---|---|---|---|
| Keycloak (staff realm) | `iam-service/iam-staff-portal-api/.../helpers/keycloak_helper.py` | Admin REST wrapper: create/delete client, create/delete client role. Uses the *caller's bearer token*. | No user create/update/disable, no role-mappings, no groups, no execute-actions (temp password / verify email). No service-account auth. |
| IAM staff portal API | `iam-service/iam-staff-portal-api` (`/applications`, `/user-access`, `/data-policies`, login providers; roles + permissions CRUD) | Central catalogue of **applications, roles, permissions, role-permissions, data policies** (DB tables + seed JSON `staff_*.json`). | No user endpoints, no bulk. Catalogue is not the source for app-local roles. |
| IAM auth (`iam-core`, `/auth/get_user_profile`) | iam-service | Provider-neutral principal `{sub, name, client_roles:{app:[roles]}}`. | Read-only identity. |
| `oan_auth_service` | frappe app | Validates IAM tokens, JIT-creates Frappe `User` (`<sha256(key:sub)>@iam.internal`), syncs roles from `client_roles`, `oan_auth_on_delegated_login` hook. `IAM_USER_RECONCILIATION_PLAN.md` (proposed, not built) adds an identity table + Keycloak directory read. | Users appear only at first login; no pre-provisioning (explicit non-goal there). |
| `oan_da_app_services` | `/api/v1/da-services/rbac-assignments` (GET/POST/GET id/PATCH/revoke); `/agent-imports` (template, validate, import, list) | Scoped grants: role + region/zone/woreda/DA-ID + effective window, audited. Bulk DA import goes to DA Registry (two-stage approval). | Assignment needs an existing Frappe `User`. `docs/rbac.md` open points: "Roles vs approved OAN RBAC model", "Registry approval -> Services user + assignment". |
| `oan_grievance_service` | `api/v1/officer.py` (POST create officer, PATCH, reset temp password) | Officer + desk provisioning, email-keyed. | Creates local record; no Keycloak coupling visible. Own role/level vocabulary. |
| `oan_a2c` | `/v1/banks`, bank users; docs say Keycloak is SSoT for roles via `realm_access.roles` | Org onboarding (self-service). | Different role claim shape (`realm_access` vs IAM `client_roles`). |
| `registry-platform` / farmer-registry / awe / consent | `test/sanity/.../keycloak_seed.py` uses Keycloak admin API ad hoc | Precedent for scripted Keycloak seeding. | Not reusable; test-only. |

Key observation: **three different role vocabularies** (DA: Development Agent/Supervisor/Executive/OAN Administrator/Communications Officer; Grievance: Officer/Head/Reviewer + level; A2C: Bank roles) and **two claim shapes** (`client_roles` via IAM, `realm_access.roles` direct). Central RBAC must reconcile these before automating anything.

## 2. Target design

A new service, **`central-rbac`** (FastAPI, same stack as `iam-staff-portal-api`, so it can be folded into IAM later), that is the single entry point for *"who gets what, where"* and fans out to Keycloak and each app.

```
 CSV/XLSX / JSON  -->  central-rbac API  --> Keycloak Admin API (users, groups, role-mappings, actions)
 (admin UI later)       |  policy catalogue  --> IAM staff portal API (apps, roles, permissions)
                        |  plan/apply engine --> App adapters:
                        |                         - da_services  /rbac-assignments, /agent-imports
                        |                         - grievance    /officers
                        |                         - a2c          (bank users)
                        |                         - registry/farmer (Keycloak group/role only)
                        +-- audit + job store (Postgres)
```

### 2.1 Central policy model (the "central RBAC policy")
A versioned YAML/JSON file in this repo, validated by JSON Schema, loaded into Postgres:

- `roles`: canonical name, Keycloak client + client-role (matches IAM `client_roles` shape), description.
- `scopes`: `global | region | zone | woreda | da | org`, with the admin-area code source (`master-data-service`, same codes DA Services stores).
- `app_mappings`: per canonical role, per app, how it materialises, e.g.
  - `da_services`: Frappe Role + `DA RBAC Assignment {role, region/zone/woreda/da_id}`
  - `grievance`: officer `role` + `level`
  - `a2c`: bank role
  - `keycloak`: client role / group
- Resolves the open point in `docs/rbac.md` ("Role names vs the approved OAN RBAC model"). **This mapping needs sign-off from the RBAC owner before Phase 2.**

### 2.2 Bulk input
One row = one person + one or more grants:

`email, full_name, phone, employee_id?, role, scope_type, region, zone, woreda, da_id?, apps(list|default), effective_from, effective_to, send_invite(y/n)`

Same template + `validate` (dry-run) + `import` + `list imports` shape as DA Services' `/agent-imports`, so operators see one familiar flow. Idempotency key = normalized email (+ row hash) so re-uploads are safe.

### 2.3 Plan/apply engine
1. **Validate** (no side effects): schema, role exists in policy, scope codes exist, duplicate emails, caller authorised.
2. **Plan**: diff desired vs actual (Keycloak user/roles/groups; each app's current assignments). Output create/update/revoke/no-op per row. Operator reviews.
3. **Apply** per row as a saga, ordered: Keycloak user -> role/group mapping -> required actions (set password, verify email) -> app adapters in dependency order. Each step recorded; on failure mark row `partial`, retry-able, with compensating steps only where safe (never auto-delete a Keycloak user).
4. **Reconcile** (scheduled): re-read Keycloak and app state, report drift. Revocation = disable in Keycloak + revoke assignments (DA Services already revokes by deactivating, never deleting).
Guardrails borrowed from `IAM_USER_RECONCILIATION_PLAN.md`: a failed/partial/empty directory read never disables anyone; max removal percent per run; two-strike removal.

### 2.4 Keycloak adapter
Extend the pattern in `keycloak_helper.py`, but authenticate as a **service account (client-credentials)** with least-privilege `realm-management` roles (`manage-users`, `view-users`, `query-users`, `view-clients`, `map-roles` ... exact set to be confirmed), not the caller's token. Calls: `POST /users`, `GET /users?email=&exact=true`, `PUT /users/{id}`, `GET/POST/DELETE /users/{id}/role-mappings/clients/{clientUuid}`, `PUT /users/{id}/groups/{gid}`, `PUT /users/{id}/execute-actions-email`. Handle 409 (exists) as update, 429/5xx with backoff, batch concurrency cap. Secrets via env/K8s secret only.

### 2.5 App adapters
Each adapter implements `plan(row)` and `apply(row)`; auth is the app's own JWT via IAM (token of an OAN Administrator service principal) so the app's existing RBAC and audit trail still apply and *no app internals are bypassed*:

- **DA Services**: user must exist first. Because `oan_auth_service` provisions the Frappe `User` only on first login, choose one (decision D2): (a) add a pre-provision endpoint to `oan_auth_service` (creates `User` + `OAN IAM Identity` row from email+sub, which also helps the reconciliation plan), or (b) apply the DA assignment lazily on first login via the existing `oan_auth_on_delegated_login` hook, from a pending-grants table. Recommend (a). Then `POST /rbac-assignments`. DA onboarding of actual agents continues through `/agent-imports` (registry is the DA master); central-rbac only calls it for rows with role `Development Agent`.
- **Grievance**: `POST /officers` (+ temp password endpoint if not using Keycloak actions).
- **A2C**: bank-user creation endpoint to be identified (docs only show bank registration); may be Keycloak-role-only.
- **Registry / farmer-registry / awe / consent**: Keycloak group/client-role only, unless an app-level API is found.

### 2.6 Security and audit
- Only OAN Administrator (or a new `RBAC Admin` role) may call; every row, plan and apply logged append-only with actor, request id, before/after.
- No passwords in CSVs or logs; use Keycloak required actions / invite email. PII minimised in job store; retention policy.
- Four-eyes option for bulk applies above N rows or for admin-level roles.
- Rate limits + per-run caps; dry-run mandatory before apply.

## 2.7 Revision: built as a Frappe app (decided)

Replaces the FastAPI/Postgres assumption in 2.1-2.3 and decision D1. Same bench as `oan_auth_service`, `da_services`, grievance and a2c.

- **Catalogue as DocTypes**: `RBAC Role` (canonical name, scope type, `user_fields` child table, duties), child `RBAC App Mapping` (app, role/assignment template, extra fields) and Keycloak mapping (client, client role, groups, user attributes). Loaded from YAML in git via fixtures/loader so changes are PR-reviewed; the desk UI renders it, editable later once the model settles.
- **Bulk flow**: `RBAC Import Batch` + `RBAC Import Row` DocTypes. Whitelisted `validate`, `plan`, `apply`; rows processed by `frappe.enqueue` jobs with per-row status and retry. Template CSV generated per role from `user_fields`.
- **Access and audit**: `RBAC Admin` role; Frappe permissions on the DocTypes; append-only audit DocType (pattern from `DA Audit Event`).
- **Keycloak**: service-account client in a Settings single DocType (`Password` field). Adapter covers users, client-role mappings, groups, user attributes (e.g. livestock `approver_location_value`), required actions.
- **App adapters**: prefer each app's public REST endpoints (`/rbac-assignments`, `/officers`) so their RBAC and audit still apply. Direct in-process calls only if an app has no endpoint.
- **Order per row**: Keycloak user -> read back `sub` -> `OAN IAM Identity` pre-provision -> app grants.
- **Must stay optional**: each app keeps working with its own admin API when central-rbac is not installed. No app-specific business logic inside this app.

## 2.8 Scope encoding and registry findings (verified in code)

Each target stores "who, where" differently, so the catalogue needs a per-app **scope encoding**, not just a role-name map.

| Target | Role | Scope lives in | Evidence |
|---|---|---|---|
| DA Services | Frappe Role (Supervisor, Executive, ...) | `DA RBAC Assignment` row (region/zone/woreda/DA-ID, window) | `docs/rbac.md` |
| Grievance | officer `role` | `level`, `department`, `service_categories`, `region` on the officer | `api/v1/officer.py` |
| Livestock registry | Keycloak client role on `livestock-staff-portal` (Field Officer, Kebele/Woreda/Zone/Region Approver) | level in the role name + Keycloak user attribute `approver_location_value` | `scripts/keycloak_livestock_test_users.py` |
| Registry core | Keycloak client roles | data policies, published as client roles `DP_<mnemonic>` (policy = type ALLOW/DISALLOW, target REGISTER_RECORD/GEO/ATTRIBUTE, JSON filter kept in IAM/registry DB) | `data_policy_keycloak_helper.py`, `iam_core/models/data_policy.py` |
| AWE | client role `AWE_ADMIN` on `awe-admin-portal` | none | `sanity/keycloak_seed.py` |
| A2C | Keycloak `realm_access.roles` (Bank roles) | organisation | `oan_a2c/docs/identity_management_architecture.md` |

Catalogue consequences:
- Per-app mapping gets a `scope_encoding` of `role_name | user_attribute | assignment_row | data_policy | app_field`.
- Keycloak adapter must write user attributes and client-role mappings on arbitrary clients, not only the staff client.
- `DP_*` roles are **referenced, not created**, by central-rbac: policies are created in IAM/registry. Validation must fail a row whose `DP_` role does not exist.
- Not yet verified: how the registry evaluates the filter expression, and how ALLOW/DISALLOW combine across several held policies. Confirm with the registry owners before mapping scope to data policies.
- Reuse the service-account pattern in registry core's `DataPolicyKeycloakHelper` (client_credentials token, cached until expiry) for the Keycloak adapter.
- IAM staff portal API's `KeycloakHelper` only creates/deletes clients and client roles, uses the caller's token, and has no user operations; it is not a user-onboarding API.

## 2.9 Admin authentication: separate frontend + `oan_auth_service` (decided; verified in code)

The admin UI is a separate frontend, not the Frappe desk. No Social Login Key, no desk session.

**Flow.** The frontend logs in through the **staff IAM** (which proxies Keycloak, `staff` realm), not directly to Keycloak: that is how every other consumer of `oan_auth_service` works (`api/iam_tokens.py`). It sends `Authorization: Bearer <IAM access token>` (or the IAM `X-Access-Token` cookies) to central-rbac. `oan_auth_service` matches the token `iss` to an enabled `OAN IAM Service` row, calls that IAM's `GET /auth/get_user_profile`, gets `{sub, name, client_roles}`, JIT-creates/resolves the Frappe `User`, and syncs roles. Refresh and logout reuse `POST /api/v1/auth/refresh` and `/logout` with `access_token` plus forwarded IAM cookies.

**How central-rbac registers** (pattern from `api/router.py` / `register_namespace`):
- Declare routes with the app's `@route(...)` decorator under a namespace such as `/api/v1/central-rbac`, and call `ensure_routes_registered` from module import so every worker registers it (the registry is per-process memory).
- Every path under the prefix takes a JWT by default. Do not declare any `exempt_paths`, `api_key_paths` or `jwt_or_api_key_paths`: all admin routes are JWT-only, and `sid` cookies and API keys are refused.
- Optional `revocation_check(user)` to cut live tokens, e.g. when the admin is disabled in the identity table.

**Config (site data, not code).** One `OAN IAM Service` row for central-rbac: `issuer` = staff realm issuer (exact, incl. trailing slash), `client_id` = the UI's client (token `azp` must equal it), `application` = `central-rbac`, so `client_roles["central-rbac"]` becomes Frappe roles. Roles `RBAC Admin` / `RBAC Approver` must exist in Frappe, since roles not present are dropped.

**Corrections to earlier assumptions**
- IAM skips `aud`, and the staff and agent IAMs share a login-provider table. Isolation comes from the exact `issuer` match plus `client_id`/`azp`, not audience. Set `client_id` on the row, always.
- Roles from the token are not allowlisted: whoever administers the `central-rbac` client in Keycloak can grant any existing Frappe role, `System Manager` included. Mitigation: restrict who can edit that client, and add a central-rbac `before-login` check (via `on_delegated_login` / `revocation_check`) that rejects a principal holding roles outside {`RBAC Admin`, `RBAC Approver`}.
- The earlier "direct Authorization Code + PKCE to Keycloak" assumption is replaced by the IAM login; the IAM owns the Keycloak client and cookies.

**Still to do**
- Disable or leave unregistered the `oan_auth_service` admin routes (`/v1/admin/users`, `temporary-password`, `roles`, `sessions`) and local `/v1/auth/login` for this deployment. Not yet checked whether they are switchable per consumer; they exist in `gateway_apisix.py` as `auth-admin` / `auth-credentials` routes.
- Step-up auth for `apply` and revocation (recent-auth check): not provided by `oan_auth_service`; needs a claim check on the IAM principal or a re-login prompt in the UI.
- Audit identity: JIT users are `<sha256(key:sub)>@iam.internal`; store `sub`, email, name from the principal in every audit row.
- CORS/origin: put the frontend and API behind the same gateway (APISIX config exists in the app), else configure Frappe's CORS allow-list.
- Break-glass: Frappe `Administrator` via bench only, not exposed.
- Service-account credential for Keycloak and app adapters stays separate from admin tokens (2.7).

## 2.10 Target architecture: desired-state controller with per-target service accounts (proposed)

Refines 2.7 and 2.9. Where they conflict (the single `RBAC Admin` role, `RBAC Import Row` as the unit of work), this section wins.

**Identity split.** Humans log in as themselves (2.9) and hold only central-rbac roles. Central-rbac acts on targets through per-target service accounts with a narrow `RBAC Manager` role; no human holds System Manager on a target, and no target credential reaches the UI.

| Human role | Can |
|---|---|
| `RBAC Viewer` | Read catalogue, drift reports, audit |
| `RBAC Operator` | Onboard users, change assignments, run bulk batches |
| `RBAC Schema Admin` | Create / rename roles, edit doctype permissions |
| `RBAC Approver` | Approve batches and schema change sets (maker-checker: not the author) |

No write-only role: nobody should change what they cannot read.

**Targets and the adapter contract.**
- `RBAC Target` DocType: type (frappe-site, iam-staff-portal, keycloak, da-services), base URL, credential reference, contract version, last snapshot.
- Every adapter implements: list roles, list doctype permissions, list assignments, create / rename / delete role, set / remove permission. Versioned.
- Frappe sites get an `rbac_agent` module with its own role and audited endpoints. Frappe's built-in permission manager (`frappe/core/page/permission_manager`) is gated to `System Manager` on every call, so the agent wraps the same logic (writing `Custom DocPerm`) behind `RBAC Manager` instead. Role rename is allowed on Role (`allow_rename: 1`).
- Non-Frappe targets (IAM staff portal, Keycloak) get REST adapters over their existing role / client-role APIs. Not yet verified: that they expose enough, and how IAM staff portal writes interact with its `staff_*.json` seed files.

**Desired vs observed state.**
- The catalogue (2.7) is the desired state. Scheduled and on-demand snapshots per target are the observed state.
- Drift report: roles not in the catalogue, permissions changed outside central-rbac, missing mappings.

**Schema changes are change sets.**
- Flow: draft -> per-target diff -> approve -> apply -> post-apply snapshot. Rollback re-applies the pre-change snapshot.
- Each catalogue role has an immutable `role_id`; display name and per-target names are attributes.
- A rename is a migration, not an edit: create new name, dual-grant, move assignments, remove old name. An in-place rename would break live tokens and other apps' references (Frappe `rename_doc` fixes its own links only).

**Non-blocking user creation (outbox).**
1. Create the Keycloak user synchronously (identity source of truth); the user can log in at once.
2. Write one outbox row per (user, target) with state `pending | applying | applied | failed | retrying`.
3. Jobs are idempotent (keyed by row), with exponential backoff, a per-target circuit breaker, and a dead-letter state.
4. One queue per target, with its own rate limit and concurrency cap, so a slow registry does not stall the rest.
5. Lazy fallback: pending grants are applied at first login via `oan_auth_on_delegated_login` (D2 option b).
6. A batch status is a roll-up of its outbox rows; partial success and per-target retry are normal.

`frappe.enqueue` is enough for thousands of users per batch. The outbox table keeps the worker replaceable if volume outgrows it.

**Enterprise baseline.** Least privilege; maker-checker; append-only audit (who, what, where, before, after, `sub`); secrets in `Password` fields with a path to a vault; versioned contract; drift detection; per-target health and metrics; break-glass via bench only.

**Supersedes:** `RBAC Import Row` becomes the outbox row; add `RBAC Target`, `RBAC Snapshot`, `RBAC Change Set`.

## 2.11 Patterns borrowed from open-source IGA projects (read in code, 2026-11-18)

Shallow clones of Apache Syncope, Evolveum midPoint, ConnId, OpenFGA, Cerbos and the Keycloak Terraform provider were read for structure only. None is adopted as a dependency (a second identity system would conflict with Keycloak as source of truth). Not read: any runtime behaviour at scale; claims below are from source structure.

| Borrow | From | What the code shows | Where it lands |
|---|---|---|---|
| Per-target retry policy as data | Syncope `AbstractPropagationTaskExecutor.retryTemplate` | Each `ExternalResource` has a propagation policy: back-off strategy (fixed / exponential / random-jitter), initial and max interval, multiplier, max attempts; retry templates cached per resource and expired when the resource changes | Fields on `RBAC Target`, applied by the outbox worker (2.10) |
| Outbox row states + priority | Syncope `PriorityPropagationTaskExecutor`, `ExecStatus` (`CREATED, SUCCESS, FAILURE, NOT_ATTEMPTED`) | Targets run in priority order; a null priority runs async; later steps become `NOT_ATTEMPTED` when an earlier one fails | Per-row order Keycloak -> identity -> app grants (2.7); mark dependants `not_attempted`, not `failed` |
| Failed inbound changes kept for a human | Syncope `Remediation` (`createRemediationIfNeeded` in pull handlers) | A pull that cannot be applied is stored as a remediation instead of being dropped or blocking the run | Drift items we cannot auto-fix go to a remediation queue |
| Conflict policy on reconciliation | Syncope `ConflictResolutionAction` (`IGNORE, FIRSTMATCH, LASTMATCH, ALL`) | Explicit policy when several remote objects match | Matching rule when a catalogue role matches several target roles |
| Capability-based adapter SPI | ConnId `spi/operations` | A connector implements only the ops it supports: `Create, Update, Delete, Search, Sync, LiveSync, Schema, Test, UpdateAttributeValues, UpdateDelta, DiscoverConfiguration`; `getLatestSyncToken` enables incremental sync | Adapter contract (2.10) becomes capability flags per target plus `test()` and `schema()`; incremental snapshots via a sync token where the target supports it |
| Observed-state object per target | midPoint "shadow" + `pendingOperation` with grace and retention periods | A shadow is the local record of the remote object; pending async operations are kept on it, with a grace period (do not repeat an operation still in flight) and a retention period (visible after completion) | `RBAC Snapshot` / outbox row: idempotency window and retention setting |
| Role lifecycle | midPoint `lifecycleState`: `draft, proposed, active, suspended, deprecated, archived, failed` | Roles are never hard-deleted; they move through states | Catalogue role state; rename and delete become `deprecated` -> `archived` after assignments are moved |
| Policy as reviewed, tested code | Cerbos (YAML policies from disk / git / blob, compile + test before distribution; stateless decision point) | Policies are files with a compile and test step, distributed to stateless servers | Keep the YAML catalogue in git; CI validates it; the control plane distributes it |
| Authorization model versioning | OpenFGA (store + versioned authorization model; model tests in CLI) | A new model is a new immutable version, not an edit | Catalogue and change sets get immutable versions; rollback = re-apply an earlier version |
| Serialise concurrent writes to the same remote | Terraform Keycloak provider `mutex` package | A lock around writes that cannot run in parallel against Keycloak | Per-target concurrency cap and a lock for operations that touch the same realm object |
| Plan / apply / import | Terraform provider (`Importer` on resources; state vs config diff) | Existing remote objects can be imported into managed state before they are changed | First run per target imports existing roles into the catalogue as `unmanaged`, then adopts them one by one |

**Correction to 2.10.** Keycloak can rename a role in place (provider updates `name` without `ForceNew`). The rename migration is still needed because tokens, other apps' assignment rows and client configs reference the old name.

**Not borrowed.** midPoint's full expression and mapping language, Syncope's workflow engine and ConnId's JVM packaging: too heavy for a Frappe app, and our targets are REST APIs, not LDAP or JDBC.

## 3. Phases

| Phase | Deliverable | Exit check |
|---|---|---|
| 0. Decisions | Canonical role/scope matrix signed off; choose D1-D4 below | Matrix reviewed by RBAC owner |
| 1. Skeleton + Keycloak | Repo scaffold, policy schema + loader, Keycloak service-account client, user/role/group ops, single-user API | Create/disable/map one user on a dev realm, idempotent on rerun |
| 2. Bulk engine | Template, validate, plan, apply, job + row status API, retry, audit | 500-row file dry-run and apply with injected failures; rerun is a no-op |
| 3. App adapters | DA Services (+ `oan_auth_service` pre-provision endpoint), Grievance, then A2C/registries | Same person gets Keycloak + DA assignment + grievance officer from one row |
| 4. Reconcile + offboarding | Scheduled drift report, bulk revoke, safeguards | Drift detected on a manually changed user; removal caps hold |
| 5. UI (optional) | Upload/validate/review/apply screen, possibly as a tab in `iam-staff-ui` | Operator completes import without CLI |

## 4. Decisions needed from you

- **D1 Home:** decided, Frappe app (see 2.7).
- **D2 Frappe user pre-provisioning:** add endpoint to `oan_auth_service` (recommended) vs lazy apply on first login.
- **D3 Source of truth for roles:** Keycloak only (A2C's stated model) vs IAM catalogue + Keycloak. Recommend IAM catalogue drives Keycloak client roles; Keycloak is authoritative for membership.
- **D4 Scope of first release:** which apps are in v1 (suggest Keycloak + DA Services + Grievance).
- Realm/client names and who owns the Keycloak service-account credentials in each environment.

## 5. Risks / open items

- Role vocabulary mismatch (section 1) is the main risk; automation without the agreed matrix will entrench inconsistent grants.
- `iam_tokens`/user naming is one-way (`sha256(key:sub)`): central-rbac must store `sub` itself (or read it from Keycloak) to address app users.
- Keycloak `realm_access` (A2C) vs `client_roles` (IAM) claim difference needs one mapper convention.
- A2C and registry-platform user-admin endpoints were not found in this survey; confirm before committing them to scope.
- Per global instructions: no tests are included in this plan unless you ask; verification in each phase uses a dev realm and manual/dry-run checks.
