# Tables behind the central-rbac Postman calls

As of 2026-11-19, dev. Covers the IAM staff registry database, Keycloak, and the Frappe services (`oan_auth_service`, DA services) that the collection calls.

How the sources were gathered:

- **IAM registry tables:** read from the SQLAlchemy models inside the `commons-services-iam-staff-portal-api` pod (`iam_staff_portal_api/models`, `iam_core/models`). Column lists match the code, not a live `\d`. Live database introspection was not done.
- **Keycloak tables:** Keycloak's standard schema, filtered to the tables these calls touch. Not checked against the live `commons` Postgres. In Postgres they are lowercase (`user_entity`, `keycloak_role`, ...).
- **Frappe tables:** read from `oan_auth_service` (branch worktree `oan_auth_service-admin-wt`) and `oan_da_app_services` doctype JSON. Frappe stores each doctype as `tab<DocType name>`.

## 1. System map

| Collection folder | Calls go to | Stores data in |
| --- | --- | --- |
| 0. Quick start | Keycloak admin API (`kc_base_url`, realm `staff`) | Keycloak DB (Postgres on `commons`) |
| 1. IAM staff portal | `staff-iam.development.oanstaging.com` (`iam-staff-portal-api`, namespace `commons`) | IAM database `iam` on `commons-postgresql` (user `iam_user`), plus Keycloak client roles through the admin API |
| 2. Service admin API | `{{service_base_url}}/v1/admin/*` (grievance on dev) | The service's Frappe site database |
| 3. DA Services scoped role assignments | `{{service_base_url}}/v1/da-services/rbac-assignments` | The DA services Frappe site database |

The IAM service writes roles to Keycloak itself, using the **caller's own token** as the Bearer token for Keycloak's admin API. The caller therefore needs `realm-management` roles in the `staff` realm.

## 2. IAM staff registry database (`iam`, Postgres)

All `staff_*` tables inherit the base model columns `id` (int, PK), `active` (bool), `created_at`, `updated_at`.

### staff_portal_applications

One row per application that IAM manages roles for (`id` 1 = iam-staff-ui, 8 = farmer-registry-staff-portal, 15 = da-registry-staff-portal, and so on).

| Column | Type | Notes |
| --- | --- | --- |
| application_mnemonic | string, unique, not null | Doubles as the Keycloak client id |
| application_description | string | |
| icon_base64 | string | |
| width | int | |
| application_url | string | |
| api_url | string | Backend base URL, set only for registry applications |
| order | int | |
| is_self_registered | bool, default false | True for rows an application pushed in itself; the seed loader never overwrites them |

### staff_roles

| Column | Type | Notes |
| --- | --- | --- |
| role_mnemonic | string, not null | Role name; also the Keycloak client role name. Names starting `DP_` are data-policy roles and are hidden from `get_roles` |
| role_description | string | |
| application_id | int, not null | References `staff_portal_applications.id` (no database foreign key in the model) |

### staff_application_permissions

| Column | Type | Notes |
| --- | --- | --- |
| permission_mnemonic | string, not null | |
| permission_description | string | |
| application_id | int, not null | References `staff_portal_applications.id` |

### staff_role_permissions

Join table between roles and permissions.

| Column | Type | Notes |
| --- | --- | --- |
| role_id | int, not null | References `staff_roles.id` |
| permission_id | int, not null | References `staff_application_permissions.id` |

### login_providers

Identity providers shown on the login screen (ids 1 and 10 Keycloak, 11 e-Signet, 12 Fayda on dev).

| Column | Type | Notes |
| --- | --- | --- |
| id | int, PK | |
| provider_name, description, icon_base64 | string | |
| client_id | string, not null | |
| client_secret | string | Secret; do not export |
| client_private_key | bytea | Secret; do not export |
| token_endpoint_auth_method | enum, not null | |
| keymanager_app_id, keymanager_ref_id | string | |
| authorization_endpoint, userinfo_endpoint, token_endpoint, server_metadata_url, jwks_uri | string | IdP URLs |
| adapter_name, jwt_assertion_aud, scope, extra_authorize_params | string | |
| enable_pkce | bool | |
| issuer | string, not null | Canonical OIDC issuer URL |
| audiences | string | JSON-encoded list |
| oauth_callback_url | string, not null | |
| default_redirect_uri | string | |

### data_policies

Data-access policies. Each `policy_mnemonic` is published to Keycloak as a client role `DP_<policy_mnemonic>`. Not touched by the nine registry calls in the collection.

| Column | Type | Notes |
| --- | --- | --- |
| policy_id | string (uuid), PK | |
| policy_mnemonic | string, not null, indexed | |
| policy_description | string | |
| register_id | string, indexed | Required for `REGISTER_RECORD` policies |
| policy_target | string, not null, indexed | `REGISTER_RECORD`, `GEO` or `ATTRIBUTE` |
| policy_type | string, not null | `ALLOW` or `DISALLOW` |
| policy_filter_expression | JSON, not null | |
| application_id | int | |

Unique indexes: (`register_id`, `policy_mnemonic`, `policy_target`) where `register_id` is not null, and (`policy_mnemonic`, `policy_target`) where it is null.

## 3. Keycloak (realm `staff`)

Tables behind the calls, from Keycloak's standard schema.

| Table | What it holds | Touched by |
| --- | --- | --- |
| `realm` | The `staff` realm | Every admin call |
| `client` | Clients such as `iam-staff-ui`, `farmer-registry-staff-portal`, `oan-client`, `realm-management` | Client lookup by `clientId` in IAM role sync; Quick start "create client" |
| `keycloak_role` | Realm roles and client roles (`client` column set for client roles) | IAM create/delete role; Quick start "create role" |
| `user_entity` | Users | Quick start "create user"; logins |
| `credential` | Password hashes | Quick start password set; login |
| `user_role_mapping` | User to role assignments (realm and client roles) | Role grants, including `realm-admin` and `Grievance Admin` |
| `composite_role` | Composite roles such as `realm-admin` | Role evaluation at login |
| `user_attribute`, `user_required_action` | User attributes and pending actions | User create |
| `user_session`, `client_session`, `offline_user_session` | Active sessions | Login; session revoke |
| `event_entity`, `admin_event_entity` | Login events and admin events | Visible in the Keycloak console; the `LOGIN_ERROR` lines in the logs come from here |

The IAM service calls these admin endpoints under `/admin/realms/staff`: `GET /clients?clientId=...`, `POST /clients/{uuid}/roles` and the matching role delete.

## 4. Frappe services (`oan_auth_service` on grievance, and DA services)

The `/v1/admin/*` calls manage users and roles on a Frappe site, so they use Frappe core tables plus three `OAN` doctypes. The login step (`/v1/auth/login`) also writes `tabOAN User Refresh Token`.

### Frappe core tables used by `/v1/admin/*`

| Table | Used for | Endpoints |
| --- | --- | --- |
| `tabUser` | Users | `GET/POST /users`, `GET/PATCH/DELETE /users/{id}`, temporary password |
| `tabHas Role` | A user's roles | `GET/POST /users/{id}/roles`, `DELETE /users/{id}/roles/{role}` |
| `tabRole` | Roles of this service (name starts with the service prefix) | `GET/POST /roles`, `GET/PATCH/DELETE /roles/{name}` |
| `tabContact` (and its email and phone child tables) | Contact record created per user | Create and delete user |
| `tabCustom DocPerm`, `tabDocPerm` | Permission rules per role and doctype | `GET/PUT/DELETE /permissions`, `POST /permissions/reset`, `GET /permissions/catalogue` |
| `tabUser Permission` | Per-user record restrictions | `/users/{id}/permissions` |
| `tabDocType` | Doctype lookup for the permission catalogue | `GET /permissions/catalogue` |

### OAN doctypes (`oan_auth_service`)

**tabOAN IAM Service:** one row per IAM-enabled service.

| Field | Type |
| --- | --- |
| key, label, icon, sort_order, enabled | Data, Data, Attach Image, Int, Check |
| base_url, request_timeout, provider_id, issuer, client_id, cookie_prefix | Data, Int, Int, Data, Data, Data |
| userinfo_url, jwks_url | Data |
| application | Data |
| default_roles | Table MultiSelect of `OAN IAM Service Role` |

**tabOAN IAM Service Role:** child table with one field, `role` (Link to Role).

**tabOAN User Refresh Token:** `user` (Link to User), `token_hash`, `expires_at`, `remember_me`. Rows are deleted by "Revoke sessions".

**tabOAN Idempotency Key:** `key_hash`, `method`, `path`, `fingerprint`, `status_code`, `response`.

### DA services (`/v1/da-services/rbac-assignments`)

**tabDA RBAC Assignment:** scoped role grants.

| Field | Type |
| --- | --- |
| user | Link to User |
| role | Link to Role |
| active | Check |
| da_id | Read Only |
| region_scope, zone_scope, woreda_scope | Links to DA Region, DA Zone, DA Woreda |
| effective_from, effective_to | Date |
| assigned_by | Link to User |
| notes | Small Text |

Rows are filtered by `rbac_assignment_query_conditions` and `has_rbac_assignment_permission` (`da_services/permissions.py`).

## 5. Call to table map

| Folder / call | Reads | Writes |
| --- | --- | --- |
| IAM login (L1 to L7) | `login_providers`; Keycloak `user_entity`, `credential`, `user_session` | Keycloak `user_session`, `event_entity` |
| IAM `get_applications` | `staff_portal_applications` | none |
| IAM `get_roles` | `staff_roles` (hides `DP_%`) | none |
| IAM `create_role` | `staff_roles`, `staff_portal_applications`; Keycloak `client` | `staff_roles`; Keycloak `keycloak_role` |
| IAM `delete_role` | `staff_roles`, `staff_portal_applications` | `staff_roles`, `staff_role_permissions`; Keycloak `keycloak_role` |
| IAM `get_permissions` | `staff_application_permissions` | none |
| IAM `create_permission` | `staff_application_permissions` | `staff_application_permissions` (no Keycloak call) |
| IAM `delete_permission` | `staff_application_permissions` | `staff_application_permissions`, `staff_role_permissions` |
| IAM `get_role_permissions` | `staff_role_permissions`, `staff_roles`, `staff_application_permissions` | none |
| IAM `create_role_permission` | `staff_roles`, `staff_application_permissions`, `staff_role_permissions` | `staff_role_permissions` |
| IAM `delete_role_permission` | `staff_role_permissions`, `staff_roles`, `staff_application_permissions` | `staff_role_permissions` |
| Service admin: users and roles | `tabUser`, `tabHas Role`, `tabRole`, `tabContact` | same tables; `tabOAN User Refresh Token` on revoke |
| Service admin: permissions | `tabCustom DocPerm`, `tabDocPerm`, `tabDocType` | `tabCustom DocPerm` |
| DA RBAC assignments | `tabDA RBAC Assignment` | `tabDA RBAC Assignment` |

## 6. Known gaps

- IAM column lists come from code. A live `\d` on the `iam` database would confirm indexes, defaults and any columns added by migrations.
- The Keycloak table names are the standard schema, not verified against the live database.
- IAM deletes of roles and permissions are hard deletes (`session.delete`); the `active` flag is not used for deletion.
- `create_role` returns HTTP 200 with `response_status: ERROR` when the Keycloak sync fails, so a 200 alone does not mean a row was written.
- The grievance service's own Frappe tables were taken from the `oan_auth_service` source. The grievance doctypes themselves are not covered here.

## 7. Data our API runs created, and where it lives

Values below were read back through the APIs (IAM list calls and Keycloak admin GET), not straight from the databases. Database access was not available, so the table and column names for Keycloak come from its standard schema. Dates are 2026-11-19 (UTC) unless noted.

### Where each system keeps its data

| System | Database | Server | Cluster / namespace |
| --- | --- | --- | --- |
| IAM staff portal API | `iam` (Postgres, user `iam_user`) | `commons-postgresql` | `openg2p_dev`, namespace `commons` |
| Keycloak (realm `staff`) | `keycloak` (Postgres, user `keycloakuser`) | `commons-postgresql` (same server as `iam`) | `openg2p_dev`, namespace `commons` |
| Grievance and A2C services | Frappe site database (not inspected) | on the `oan_dev` cluster | `oan_dev` |

### IAM registry (database `iam`)

| Record | Table | Row and columns |
| --- | --- | --- |
| Role `rbac-test-role` on app 8 | `staff_roles` | `id` 66, `role_mnemonic` `rbac-test-role`, `role_description`, `application_id` 8, `active` true |
| Permission `rbac-test-permission` on app 8 | `staff_application_permissions` | `id` 327, `permission_mnemonic` `rbac-test-permission`, `permission_description`, `application_id` 8, `active` true |
| Role to permission mapping | `staff_role_permissions` | `id` 50384, `role_id` 66, `permission_id` 327, `active` true, `created_at` 12:39:36 |

An earlier run also created permission id 326 in `staff_application_permissions` and then deleted it, so that row no longer exists.

### Keycloak (database `keycloak`, realm `staff`, realm id `fdd9135a-1cd9-4755-98ec-369a8b7d2d5e`)

| Record | Table | Row and columns |
| --- | --- | --- |
| Client `rbac-test-client` | `client` | `id` `002669bf-6588-4b4c-82d5-2c492357377a`, `client_id` `rbac-test-client`, `realm_id`, `enabled` true; extra settings in `client_attributes` (for example `client.secret.creation.time`) |
| Client role `rbac-test-role` on `rbac-test-client` | `keycloak_role` | `id` `e26f80d5-0fec-45b2-91f2-af8678165008`, `name` `rbac-test-role`, `description`, `client_role` true, `client` `002669bf-...` |
| Client role `rbac-test-role` on `farmer-registry-staff-portal` (written by the IAM role sync) | `keycloak_role` | `id` `8d3b0977-25bd-451a-975c-5ce34f5fa536`, `name` `rbac-test-role`, `client_role` true, `client` `87d2cb6f-8423-4d63-9bcf-d8f464b69c90` |
| User `rbac.test.user` | `user_entity` | `id` `9094912a-11cc-4454-bc44-0cdda6e9ec19`, `username`, `email` `rbac.test.user@example.com`, `enabled`, `email_verified`, `created_timestamp` 1795083774769 |
| User `rbac.admin` | `user_entity` | `id` `01fc081c-bbef-42f3-a605-92ae4e1c8e23`, `username`, `email` `rbac.admin@example.com`, `enabled`, `email_verified`, `created_timestamp` 1795091226788 |
| Passwords for both users | `credential` | `user_id`, `type` `password`, hashed `secret_data` and `credential_data` (not readable) |
| `rbac.test.user` role grant | `user_role_mapping` | 1 row: `user_id` `9094912a-...`, `role_id` `e26f80d5-...` |
| `rbac.admin` role grants | `user_role_mapping` | 26 rows: 2 realm roles (`default-roles-staff`, `AGGREGATION_STAFF_VIEWER`) and 24 client roles across 19 clients, including `realm-admin` on `realm-management` and `Grievance Admin` on `oan-client` |

### Grievance and A2C services (Frappe site databases)

Nothing was written in this session. Role create returned 404 at the gateway, so no `tabRole` or `tabCustom DocPerm` rows exist from it. Earlier sessions created a grievance user through `POST /v1/admin/users` (`tabUser`, `tabHas Role`, `tabContact`); I did not verify those rows or clean them up.
