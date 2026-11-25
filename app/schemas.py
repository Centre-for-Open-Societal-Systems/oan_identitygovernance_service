from pydantic import BaseModel, EmailStr, Field


class ApplicationIn(BaseModel):
    realm: str
    client_id: str = Field(pattern=r"^[A-Za-z0-9._-]+$")  # the Keycloak clientId, one per service
    description: str | None = None  # stored on the Keycloak client
    admin_api_url: str  # the service's admin API, e.g. https://grievance-service.<domain>/v1/admin; the client's root URL


class RoleIn(BaseModel):
    name: str


class RoleRef(BaseModel):
    """A role is the Keycloak client role `name` of application `client_id`, in the user's realm."""

    client_id: str
    name: str


class PermissionPair(BaseModel):
    resource: str
    action: str


class RolePermissionsIn(BaseModel):
    permissions: list[PermissionPair]


class UserIn(BaseModel):
    realm: str
    username: str
    email: EmailStr | None = None
    first_name: str | None = None
    last_name: str | None = None
    groups: list[str] = []
    location_codes: list[str] = []
    roles: list[RoleRef] = []
    send_invite: bool = True


class BulkUsersIn(BaseModel):
    users: list[UserIn]


class RoleAssignIn(RoleRef):
    pass


class ApplicationOut(BaseModel):
    """The application's Keycloak client, read live."""

    realm: str
    client_id: str
    admin_api_url: str | None
    description: str | None
    enabled: bool

    @classmethod
    def of(cls, realm: str, client: dict):
        return cls(
            realm=realm, client_id=client["clientId"], admin_api_url=client.get("rootUrl") or None,
            description=client.get("description") or None, enabled=client.get("enabled", True),
        )


class ApplicationCreatedOut(ApplicationOut):
    client_secret: str | None  # None for a public client
    client_secret_note: str = "Shown once. Not stored."


class RoleOut(BaseModel):
    """A client role of the application's Keycloak client, read live."""

    client_id: str
    name: str
    description: str | None


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
