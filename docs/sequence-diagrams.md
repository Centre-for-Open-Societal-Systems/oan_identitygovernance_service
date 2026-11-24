# Sequence diagrams

How the service behaves today, route by route. The design doc (`central-rbac-system-design.md`) has earlier, higher-level versions of the onboarding flows.

Participants: **Operator** (a person or script in the `staff` realm holding an `rbac-*` role on the `central-rbac` client), **RBAC** (this service), **KC** (Keycloak), **Auth service** (`oan_auth_service` admin API under `/v1/admin`, the managed service, called with the operator's own token).

## 1. Operator login

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant KC as Keycloak

    Op->>RBAC: GET /auth/login
    RBAC-->>Op: 302 to KC authorize (PKCE challenge, state cookie)
    Op->>KC: sign in
    KC-->>Op: 302 /auth/callback?code
    Op->>RBAC: GET /auth/callback?code&state
    RBAC->>KC: token (code + PKCE verifier + client secret)
    KC-->>RBAC: access + refresh token
    RBAC-->>Op: Set-Cookie rbac_access, rbac_refresh; 302 to post-login URL
    Note over Op,RBAC: Scripts skip this and send Authorization: Bearer <token>.<br/>Cookie writes also need x-requested-with: central-rbac.
```

## 2. Every request

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant KC as Keycloak

    Op->>RBAC: request + Bearer token or rbac_access cookie
    RBAC->>KC: fetch signing keys (cached)
    RBAC->>RBAC: verify RS256, issuer, azp = central-rbac
    RBAC->>RBAC: roles = resource_access.central-rbac.roles
    alt missing or invalid token
        RBAC-->>Op: 401 AUTH_REQUIRED / AUTH_INVALID / AUTH_EXPIRED
    else role not allowed for this route
        RBAC-->>Op: 403 FORBIDDEN
    else allowed
        RBAC-->>Op: 200 {response_header, response_body}
    end
```

## 3. Register an application (with first sync)

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant DB as RBAC database
    participant KC as Keycloak

    Op->>RBAC: POST /applications/create {realm, mnemonic, admin_api_url}
    RBAC->>KC: create client (id = mnemonic, service account on)
    alt client already exists
        RBAC->>KC: find client by id
    end
    RBAC->>KC: read client secret
    RBAC->>KC: create client role OAN Administrator, grant it to the operator (sub)
    RBAC->>DB: insert application, audit row
    RBAC->>KC: list the client's roles
    RBAC->>DB: insert each as role (status imported), sync_run, audit row
    RBAC-->>Op: application + client secret (shown once)
```

## 4. Create a role in a managed service

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant DB as RBAC database
    participant KC as Keycloak
    participant SV as Auth service

    Op->>RBAC: POST /roles/create {application_id, name}
    RBAC->>KC: is the application's client enabled?
    RBAC->>DB: insert role (status pending)
    RBAC->>SV: POST /roles {role}
    alt service accepts
        SV-->>RBAC: 201
        RBAC->>DB: status active
    else service refuses, is unreachable, or no admin_api_url
        RBAC->>DB: status failed + error text
    end
    RBAC->>DB: audit row
    RBAC-->>Op: role (active or failed)
    Note over RBAC,KC: The Frappe role does not exist in Keycloak, so granting it<br/>to a user fails with KEYCLOAK_ROLE_MISSING until a client role of the<br/>same name exists there. Open item.
```

## 5. Set a role's permissions

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant DB as RBAC database
    participant SV as Auth service

    Op->>RBAC: POST /roles/{id}/permissions {permissions: [{resource, action}]}
    RBAC->>DB: role active or imported?
    RBAC->>DB: load the application's allow-list
    alt a pair is not in the allow-list
        RBAC-->>Op: 422 NOT_ALLOWED
    else all allowed
        RBAC->>SV: GET /permissions?role=
        SV-->>RBAC: current rows (doctype, rights)
        loop each doctype held today or wanted
            RBAC->>SV: PUT /permissions {role, doctype, permissions: {right: true/false}}
        end
        RBAC->>DB: audit row
        RBAC-->>Op: the permissions now set
    end
    Note over RBAC,SV: Permissions are never stored here. GET /roles/{id}/permissions reads them live.
```

## 6. Create a user

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant DB as RBAC database
    participant KC as Keycloak

    Op->>RBAC: POST /users/create {realm, username, email, role_ids, send_invite}
    RBAC->>DB: realm registered? roles active or imported?
    RBAC->>KC: create user
    alt username or email exists
        RBAC->>KC: find the existing user (re-run is safe)
    end
    loop each application of the chosen roles
        RBAC->>KC: list the client's roles
        RBAC->>KC: add client roles to the user
    end
    opt send_invite
        RBAC->>KC: execute-actions-email (set password, verify email)
    end
    RBAC->>DB: audit row
    RBAC->>KC: read the user back
    RBAC-->>Op: user
```

## 7. On-demand sync

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant DB as RBAC database
    participant KC as Keycloak

    Op->>RBAC: POST /sync/run/{app_id}
    RBAC->>KC: list the client's roles
    RBAC->>DB: add roles not yet known (status imported); fill missing Keycloak ids
    RBAC->>DB: sync_run (counts, errors), audit row
    RBAC-->>Op: sync run
    Op->>RBAC: GET /sync/status?realm=
    RBAC-->>Op: latest 50 runs
    Note over RBAC,KC: One direction only (Keycloak to RBAC), roles only. Users and permissions<br/>are read live, so there is nothing else to copy. Scheduled runs and the drift report are not built.
```
