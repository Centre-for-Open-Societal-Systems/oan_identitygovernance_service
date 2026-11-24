# Naming Options for central-rbac

What the service does: it manages roles and users centrally, syncs them with Keycloak, and pushes them out to the managed services (IAM and others).

All names use the `oan-` prefix.

## Short names

| Name | Notes |
|---|---|
| **`oan-access-control`** | Recommended. Plain, standard, says exactly what the service does. |
| `oan-role-management` | Literal and clear. |
| `oan-access-management` | Common enterprise term; slightly overlaps with what Keycloak does. |
| `oan-unified-rbac` | Keeps "RBAC", swaps "central" for a more enterprise-sounding word. |
| `oan-access-governance` | Formal enterprise term, a bit longer. |

## Descriptive names

| Name | Fits best when |
|---|---|
| **`oan-access-governance-service`** | Recommended. "Access governance" is the standard enterprise term (SailPoint, Saviynt, Microsoft Entra) for a central system that decides who gets which roles across many applications. It is not confused with Keycloak, the identity provider. |
| `oan-entitlement-service` | The focus is permissions and allow-lists rather than roles. |
| `oan-identity-governance-service` | Access reviews, certification or reconciliation reports are planned. The report-only reconciliation already points this way. |
| `oan-access-control-hub` | A plain, easy-to-understand name is preferred. |
| `oan-authorization-admin` | The name should make clear this is the admin/control side, not the runtime policy check. |
| `oan-role-management-service` | The literal description, with no extra meaning. |

## More options

| Name | Notes |
|---|---|
| `oan-access-hub` | Short; the central place for access. |
| `oan-role-registry` | Stresses that it is the source of truth for roles. |
| `oan-role-sync` | Stresses syncing roles across Keycloak and services; undersells the admin side. |
| `oan-access-admin` | Admin console for access across services. |
| `oan-permission-management` | Focus on permissions rather than roles. |
| `oan-user-access` | Plain, user-centric. |
| `oan-access-directory` | Directory of who has what access. |
| `oan-authorization-service` | Standard term; may read as a runtime authz check. |
| `oan-access-provisioning` | Stresses pushing roles out to services. |
| `oan-role-governance` | Mix of role management and governance. |

## Governance-style names

| Name | Notes |
|---|---|
| **`oan-role-governance`** | Recommended. Clear and accurate for central role control. |
| `oan-access-governance` | Broader: covers users and permissions too. |
| `oan-identity-governance` | Industry term (IGA); implies users are in scope. |
| `oan-permission-governance` | Stresses the permission allow-list. |
| `oan-entitlement-governance` | Enterprise term for roles + permissions combined. |
| `oan-rbac-governance` | Keeps "RBAC" explicit. |
| `oan-role-administration` | Same meaning with a more operational tone. |
| `oan-role-control` | Shorter, with the same intent. |

## Short "central" names

| Name | Notes |
|---|---|
| **`oan-central-roles`** | Recommended. Short and says exactly what it is. |
| `oan-central-access` | Covers users and permissions too. |
| `oan-central-authz` | Short; "authz" is a standard term for authorization. |
| `oan-central-iam` | Avoid: clashes with the existing IAM service. |
| `oan-role-central` | Same words in reverse order; reads less naturally. |
| `oan-core-roles` | "Core" as a shorter stand-in for "central". |
| `oan-roles-hub` | "Hub" implies central without the word. |

## Names to avoid

- **Anything with "IAM"**: clashes with the existing IAM staff portal service.
- **`oan-policy-*` or `*-pdp`**: suggests a runtime policy engine like OPA, which this is not.
- **`oan-access-manager`**: easily confused with Keycloak's own role.
