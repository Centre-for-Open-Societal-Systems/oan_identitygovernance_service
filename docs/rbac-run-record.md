# What the central-rbac Postman runs wrote, and where

Record of the API calls we ran against dev on 2026-11-19 (times UTC): which database and table each record landed in, the column values, and which service wrote it.

How to read it:

- **[V]** read back through an API after the call.
- **[I]** inferred from the service code or Keycloak's standard schema. No direct database access was available, so no row below was read from a database.
- A table shows only the rows our runs added. Other rows in the same tables are not shown.

## 1. Services and databases

| Service | What it is | Database | Server and place |
| --- | --- | --- | --- |
| `iam-staff-portal-api` | IAM staff portal API, `staff-iam.development.oanstaging.com` | `iam` | `commons-postgresql`, `openg2p_dev`, namespace `commons` |
| Keycloak | `keycloak.development.oanstaging.com`, realm `staff` (realm `master` for admin tokens) | `keycloak` | `commons-postgresql`, `openg2p_dev`, namespace `commons` |
| `oan_auth_service` (grievance) | Frappe app behind the APISIX gateway, `grievance-service.development.oanstaging.com` | Frappe site database (not inspected) | `oan_dev` cluster |

Calls between services that we triggered:

| From | To | What for |
| --- | --- | --- |
| Postman | IAM staff API | `/auth/*` login steps and `/applications/*` registry calls |
| Postman | Keycloak | Login form (L3, L4) and the quick start admin calls |
| IAM staff API | Keycloak token endpoint | The login callback (L5) exchanges the code for tokens |
| IAM staff API | Keycloak admin API | Role create and delete: looks up the client, then creates or deletes the client role. It sends the caller's own token, so the caller needs `realm-management` roles |
| Postman | APISIX gateway, then `oan_auth_service` | Grievance `/v1/auth/login` and `/v1/admin/*` |

## 2. IAM database `iam`

### Table `staff_portal_applications`

| id | application_mnemonic |
| --- | --- |
| 8 | farmer-registry-staff-portal |

Not written by the runs. Row 8 already existed and is the `application_id` that the new role and permission rows point to. It is read by `get_applications`, and `create_role` and `delete_role` look it up to find the Keycloak client, since `application_mnemonic` is the client id. Other columns are in `rbac-tables.md`.

### Table `staff_roles`

| id | role_mnemonic | role_description | application_id | active | created_at |
| --- | --- | --- | --- | --- | --- |
| 66 | rbac-test-role | Created by the central-rbac Postman collection | 8 | true | 12:39:27 |

Written by `iam-staff-portal-api`, call `POST /applications/create_role`, as `rbac.admin`. [V]

### Table `staff_application_permissions`

| id | permission_mnemonic | permission_description | application_id | active | created_at |
| --- | --- | --- | --- | --- | --- |
| 327 | rbac-test-permission | Created by the central-rbac Postman collection | 8 | true | 12:39:29 |
| ~~326~~ | ~~rbac-test-permission~~ | ~~same~~ | ~~8~~ | ~~true~~ | ~~about 12:00, deleted afterwards~~ |

Written by `iam-staff-portal-api`, call `POST /applications/create_permission`. Row 327 was created as `rbac.admin`, row 326 as `admin` and removed by `delete_permission`. [V]

### Table `staff_role_permissions`

| id | role_id | permission_id | active | created_at |
| --- | --- | --- | --- | --- |
| 50384 | 66 | 327 | true | 12:39:36 |

Written by `iam-staff-portal-api`, call `POST /applications/create_role_permission`, as `rbac.admin`. [V]

## 3. Keycloak database `keycloak` (realm `staff`, realm id `fdd9135a-1cd9-4755-98ec-369a8b7d2d5e`)

### Table `client`

| id | client_id | realm_id | enabled |
| --- | --- | --- | --- |
| 002669bf-6588-4b4c-82d5-2c492357377a | rbac-test-client | fdd9135a-… | true |

Written by Postman quick start "Create client", directly to Keycloak's admin API, about 10:22 (earlier run). Its `client_attributes` table also got `client.secret.creation.time` = 1795083753. [V]

### Table `keycloak_role`

| id | name | description | client_role | client |
| --- | --- | --- | --- | --- |
| e26f80d5-0fec-45b2-91f2-af8678165008 | rbac-test-role | Created by the central-rbac Postman collection | true | 002669bf-… (`rbac-test-client`) |
| 8d3b0977-25bd-451a-975c-5ce34f5fa536 | rbac-test-role | Created by the central-rbac Postman collection | true | 87d2cb6f-8423-4d63-9bcf-d8f464b69c90 (`farmer-registry-staff-portal`) |

Row 1: Postman quick start "Create client role", directly to Keycloak (about 10:22). Row 2: written by **`iam-staff-portal-api`** calling Keycloak's admin API during `create_role` at 12:39:27. [V]

### Table `user_entity`

| id | username | email | enabled | email_verified | created_timestamp |
| --- | --- | --- | --- | --- | --- |
| 9094912a-11cc-4454-bc44-0cdda6e9ec19 | rbac.test.user | rbac.test.user@example.com | true | true | 1795083774769 (about 10:22:54) |
| 01fc081c-bbef-42f3-a605-92ae4e1c8e23 | rbac.admin | rbac.admin@example.com | true | true | 1795091226788 (about 12:27) |

Row 1: Postman quick start "Create user". Row 2: my direct Keycloak admin API call, not a Postman collection call. [V]

### Table `credential`

| user_id | type | secret |
| --- | --- | --- |
| 9094912a-… (`rbac.test.user`) | password | hashed, not readable |
| 01fc081c-… (`rbac.admin`) | password | hashed, not readable |

Row 1: quick start "Set user password". Row 2: my direct Keycloak admin API call. [I]

### Table `user_role_mapping`

| user_id | role_id | role |
| --- | --- | --- |
| 9094912a-… (`rbac.test.user`) | e26f80d5-… | `rbac-test-role` on `rbac-test-client` |
| 01fc081c-… (`rbac.admin`) | (2 rows) | realm roles `default-roles-staff`, `AGGREGATION_STAFF_VIEWER` |
| 01fc081c-… (`rbac.admin`) | (24 rows, 19 clients) | client roles, including `realm-admin` on `realm-management` and `Grievance Admin` on `oan-client` |

Row 1: quick start "Map client role to user". Rows 2 and 3: my direct Keycloak admin API calls. [V]

### Created and removed

| Table | Row | Removed by |
| --- | --- | --- |
| `user_entity` | `sub.test.user` | my cleanup call, right after the id test |
| `user_entity` | `sub.test.user2` | my cleanup call, right after the partial-import test |

## 4. Grievance Frappe database

No rows were created this session. Role create returned 404 at the gateway (routes are now exposed; rerun pending), and the `POST /v1/admin/users` test with an empty body was rejected with a 400. Earlier sessions created a grievance user (`tabUser`, `tabHas Role`, `tabContact`); those rows were not checked or cleaned up.

## 5. Call by call

### Folder 1, IAM staff portal

| Call | Handled by | Calls onward | Writes | What we saw |
| --- | --- | --- | --- | --- |
| L1 `get_login_providers` | IAM API | none | none (reads `login_providers`) | 200 |
| L2 `start_authentication_transaction` | IAM API | none | none known [I] | 200 with `id=1`. Empty id gave 400, `id=10` gave 500 |
| L3, L4 Keycloak login form and sign-in | Keycloak | none | `user_session`, `client_session` and a login event row [I] | 200, then 302 |
| L5 `/auth/callback` | IAM API | Keycloak token endpoint | none in `iam` known [I] | 307 with `X-Access-Token` and `X-CSRF-Token` cookies |
| L6, L7 `get_logged_in_user`, `get_user_profile` | IAM API | none | none | 200 |
| `get_applications`, `get_roles`, `get_permissions`, `get_role_permissions` | IAM API | none | none | 200 once the CSRF fix was in; 403 before it |
| `create_role` | IAM API | Keycloak admin API | `staff_roles` 66, `keycloak_role` `8d3b0977-…` | `admin` run at 11:59: HTTP 200 with `ERROR` ("Failed to sync role to Keycloak"), nothing written, because the token lacked `realm-management`. `rbac.admin` run at 12:39: SUCCESS |
| `create_permission` | IAM API | none | `staff_application_permissions` 326 (about 12:00) and 327 (12:39) | SUCCESS both times |
| `create_role_permission` | IAM API | none | `staff_role_permissions` 50384 | 400 until the role id was set; SUCCESS at 12:39 |
| `delete_permission` | IAM API | none | removes `staff_application_permissions` 326 | SUCCESS |
| `delete_role`, `delete_role_permission` | IAM API | Keycloak admin API (`delete_role`) | would remove the rows and the Keycloak role | Never ran for real. Every attempt returned 400 on an empty id |

Calls with an empty `application_id` returned 400 and wrote nothing.

### Folder 0, Keycloak quick start

Postman to Keycloak's admin REST API only. The Create steps ran earlier today and produced the Keycloak rows in section 3. Today's rerun got 409 Conflict on the client and user (they already existed), and its Cleanup steps fired with empty ids and returned 405, so nothing was written or deleted.

### Folder 2, grievance admin API

| Call | Result | Writes |
| --- | --- | --- |
| `POST /v1/auth/login` as `Administrator` | 200, token issued | Probably a row in `tabOAN User Refresh Token` and a login log entry [I] |
| `GET /v1/admin/roles`, `/users`, `/permissions`, `/v1/auth/me` | 200 | none |
| `POST /v1/admin/roles`, `GET /v1/admin/roles/{name}`, `GET /v1/admin/permissions/catalogue` | 404 from APISIX (no route) when we ran them. The routes are now exposed (gateway PR #131), so these have not been rerun | none yet |
| `PUT` and `DELETE /v1/admin/permissions`, `POST /v1/admin/permissions/reset` | 403 `PERMISSION_DENIED` | none |
| `POST /v1/admin/users` with an empty body | 400 validation error | none |

Added to the collection after the gateway exposed the new routes (not run yet, so no results and no rows to record):

| Collection item | Route | Would write [I] |
| --- | --- | --- |
| Roles folder: Create, Get, Disable, Delete role | `POST /v1/admin/roles`, `GET`/`PATCH`/`DELETE /v1/admin/roles/{name}` | `tabRole` row |
| Reference: Permission catalogue | `GET /v1/admin/permissions/catalogue` | none |
| User permissions folder, 1. Add record restriction | `POST /v1/admin/users/{id}/user-permissions` | `tabUser Permission` row (`user`, `allow`, `for_value`, `apply_to_all_doctypes`, `applicable_for`, `is_default`) |
| User permissions folder, 2. List record restrictions | `GET /v1/admin/users/{id}/user-permissions` | none |
| User permissions folder, 3. Remove record restriction | `DELETE /v1/admin/users/{id}/user-permissions/{name}` | deletes that `tabUser Permission` row |
| Cleanup, 11. Delete user | `DELETE /v1/admin/users/{id}` | removes the quick-start user |

The three user-permission items need `restrict_doctype` (a service doctype) and `restrict_value` (an existing record of it) in the environment; they are skipped while those are empty. Both are blank in the development environment.

### Folder 3, DA Services scoped role assignments

Not run.

## 6. Not covered

- No row or column list was read from a database. A read-only query on `iam` and `keycloak` would confirm them.
- The test data above is still in dev. Only permission 326 and the two throwaway users were removed.
