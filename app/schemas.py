from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RealmIn(BaseModel):
    name: str


class ApplicationIn(BaseModel):
    realm: str
    mnemonic: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    description: str | None = None  # stored on the Keycloak client, not here
    admin_api_url: str | None = None


class RoleIn(BaseModel):
    application_id: str
    name: str
    description: str | None = None  # sent to the service, not stored here


class PermissionPair(BaseModel):
    resource: str
    action: str


class RolePermissionsIn(BaseModel):
    permissions: list[PermissionPair]


class AllowListIn(BaseModel):
    permissions: list[PermissionPair]


class UserIn(BaseModel):
    realm: str
    username: str
    email: EmailStr | None = None
    first_name: str | None = None
    last_name: str | None = None
    groups: list[str] = []
    location_codes: list[str] = []
    role_ids: list[str] = []
    send_invite: bool = True


class BulkUsersIn(BaseModel):
    users: list[UserIn]


class RoleAssignIn(BaseModel):
    role_id: str


class ApplicationOut(BaseModel):
    """Registry row plus the live Keycloak client (`enabled` is None when the client no longer exists)."""

    id: str
    realm: str
    mnemonic: str
    admin_api_url: str | None
    description: str | None
    enabled: bool | None

    @classmethod
    def of(cls, a, client: dict | None):
        client = client or {}
        return cls(
            id=a.id, realm=a.realm, mnemonic=a.mnemonic, admin_api_url=a.admin_api_url,
            description=client.get("description") or None, enabled=client.get("enabled"),
        )


class ApplicationCreatedOut(ApplicationOut):
    client_secret: str
    client_secret_note: str = "Shown once. Not stored."


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    application_id: str
    name: str
    keycloak_role_id: str | None
    status: str
    error: str | None


class SyncRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    realm: str
    direction: str
    started: datetime
    finished: datetime | None
    counts: dict | None
    errors: list | None


class UserOut(BaseModel):
    id: str
    username: str | None
    email: str | None
    first_name: str | None
    last_name: str | None
    enabled: bool
    location_codes: list[str]

    @classmethod
    def of(cls, rep: dict):
        """From a Keycloak user representation."""
        return cls(
            id=rep["id"], username=rep.get("username"), email=rep.get("email"),
            first_name=rep.get("firstName"), last_name=rep.get("lastName"), enabled=rep.get("enabled", True),
            location_codes=(rep.get("attributes") or {}).get("location_codes", []),
        )
