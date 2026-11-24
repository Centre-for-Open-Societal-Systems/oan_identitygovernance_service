from openg2p_fastapi_common.config import Settings as BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="rbac_", env_file=".env", extra="allow", env_nested_delimiter="__"
    )

    openapi_title: str = "Central RBAC"
    openapi_description: str = (
        "Central role and user governance over Keycloak and managed services."
    )

    keycloak_url: str = "http://localhost:8080"
    keycloak_admin_realm: str = "master"
    keycloak_client_id: str = "central-rbac"
    keycloak_client_secret: str = ""
    # Direct login (backend-for-frontend): the confidential central-rbac client in master,
    # same client id/secret as the service account above.
    auth_realm: str = "master"
    auth_client_id: str = "central-rbac"
    public_url: str = "http://localhost:8000"  # externally visible base of this API (for the OIDC redirect_uri)
    post_login_url: str = "/"  # where the browser lands after login
    cookie_secure: bool = True  # set false only for plain-http local dev
    cookie_domain: str | None = None
    # Frontend origins live in `cors_allow_origins` (library setting); also checked on cookie-authenticated writes.
    # Optional second credential: access token cookie issued by the staff IAM.
    iam_issuer: str = (
        ""  # e.g. https://keycloak.<domain>/realms/staff (exact, as in the token iss)
    )
    iam_client_id: str = ""  # token azp must equal this
    iam_cookie_name: str = "X-Access-Token"  # TODO: confirm against the IAM service
    http_timeout: float = 15.0
