from pydantic import BaseModel, EmailStr, Field


class RealmIn(BaseModel):
    name: str


class ApplicationIn(BaseModel):
    realm: str
    mnemonic: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    description: str | None = None
    service_url: str | None = None
    admin_api_url: str | None = None
    admin_api_secret_ref: str | None = None
    owner: str | None = None


class RoleIn(BaseModel):
    application_id: str
    name: str
    description: str | None = None


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
