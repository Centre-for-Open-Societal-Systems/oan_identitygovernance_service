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

## 3. Register an application

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant DB as RBAC database
    participant KC as Keycloak

    Op->>RBAC: POST /applications/create {realm, client_id, admin_api_url}
    RBAC->>RBAC: realm in managed_realms?
    alt client does not exist
        RBAC->>KC: create client (service account on, rootUrl = admin_api_url, rbac.managed = true)
    else client exists
        RBAC->>KC: set rootUrl = admin_api_url and rbac.managed = true on it
    end
    RBAC->>KC: read client secret (none for a public client)
    RBAC->>KC: create client role OAN Administrator, grant it to the operator (sub)
    RBAC->>DB: audit row
    RBAC-->>Op: application + client secret (shown once)
    Note over RBAC,KC: One client per service. The application is the client itself: nothing about it<br/>is stored here. Its roles are listed live (GET /applications/{realm}/{client_id}/roles/list).
```

## 4. Create a role in a managed service

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant DB as RBAC database
    participant KC as Keycloak
    participant SV as Auth service

    Op->>RBAC: POST /applications/{realm}/{client_id}/roles/create {name}
    RBAC->>KC: the client: managed, enabled, rootUrl (the admin API)
    RBAC->>SV: POST /roles {role} (operator's token)
    SV->>SV: create the Frappe role
    SV->>KC: create the client role of the same name (its login option's admin client)
    SV-->>RBAC: 201, or an error passed back to the operator
    RBAC->>DB: audit row
    RBAC->>KC: read the role back from the client
    alt role in Keycloak
        RBAC-->>Op: role {application_id, name}
    else service created no Keycloak role
        RBAC-->>Op: 502 KEYCLOAK_ROLE_MISSING (admin_client_* not set on the login option)
    end
    Note over RBAC,SV: Nothing about a role is stored here. Keycloak says which roles exist,<br/>the service says what they may do. Delete goes the same way: POST .../roles/{name}/delete.
```

## 5. Set a role's permissions

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant DB as RBAC database
    participant KC as Keycloak
    participant SV as Auth service

    Op->>RBAC: POST /applications/{realm}/{client_id}/roles/{name}/permissions {permissions: [{resource, action}]}
    RBAC->>KC: the client's rootUrl (the admin API)
    RBAC->>SV: GET /permissions?role=
    SV-->>RBAC: current rows (doctype, rights)
    loop each doctype held today or wanted
        RBAC->>SV: PUT /permissions {role, doctype, permissions: {right: true/false}}
    end
    alt the service refuses a doctype or right
        SV-->>RBAC: 4xx
        RBAC-->>Op: 502 SERVICE_ERROR with the service's answer
    else
        RBAC->>DB: audit row
        RBAC-->>Op: the permissions now set
    end
    Note over RBAC,SV: The service decides what may be granted (its own prefix and doctype checks).<br/>Permissions are never stored here. GET .../roles/{name}/permissions reads them live.
```

## 6. Create a user

```mermaid
sequenceDiagram
    actor Op as Operator
    participant RBAC
    participant DB as RBAC database
    participant KC as Keycloak

    Op->>RBAC: POST /users/create {realm, username, email, roles: [{client_id, name}], send_invite}
    RBAC->>KC: realm managed? each client a managed application?
    RBAC->>KC: create user
    alt username or email exists
        RBAC->>KC: find the existing user (re-run is safe)
    end
    loop each application of the chosen roles
        RBAC->>KC: list the client's roles (a missing one is 409 KEYCLOAK_ROLE_MISSING)
        RBAC->>KC: add client roles to the user
    end
    opt send_invite
        RBAC->>KC: execute-actions-email (set password, verify email)
    end
    RBAC->>DB: audit row
    RBAC->>KC: read the user back
    RBAC-->>Op: user
```
