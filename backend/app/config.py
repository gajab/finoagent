"""Application configuration — loads from env vars, .env file, or GCP Secret Manager.

Priority order (highest → lowest):
  1. Environment variables (set by Cloud Run, Docker, or shell)
  2. GCP Secret Manager  (when GCP_PROJECT_ID is set and google-cloud-secret-manager installed)
  3. .env file           (local development only — never commit this file)
  4. Defaults below
"""

import logging
import os

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GCP Secret Manager helper — optional dependency, graceful fallback
# ---------------------------------------------------------------------------

def _load_gcp_secrets(project_id: str, secret_names: list[str]) -> dict[str, str]:
    """Fetch secrets from GCP Secret Manager, return as {SECRET_NAME: value} dict.

    Silently returns an empty dict if:
    - google-cloud-secret-manager is not installed
    - Credentials / network not available (local dev)
    """
    try:
        from google.cloud import secretmanager  # type: ignore
    except ImportError:
        return {}

    client = secretmanager.SecretManagerServiceClient()
    result: dict[str, str] = {}
    for name in secret_names:
        # Only fetch if not already set via a real env var
        if os.environ.get(name):
            continue
        try:
            secret_path = f"projects/{project_id}/secrets/{name}/versions/latest"
            response = client.access_secret_version(name=secret_path)
            result[name] = response.payload.data.decode("utf-8").strip()
        except Exception as exc:  # noqa: BLE001
            logger.debug("GCP Secret Manager: could not fetch %s — %s", name, exc)
    return result


# ---------------------------------------------------------------------------
# Secrets that can live in GCP Secret Manager (same names as env vars)
# ---------------------------------------------------------------------------
_GCP_SECRET_NAMES = [
    "SECRET_KEY",
    "ENCRYPTION_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "DATABASE_URL",
    "SMTP_PASS",
]

# Inject GCP secrets into env before pydantic-settings reads them
_gcp_project = os.environ.get("GCP_PROJECT_ID", "")
if _gcp_project:
    _fetched = _load_gcp_secrets(_gcp_project, _GCP_SECRET_NAMES)
    for _k, _v in _fetched.items():
        os.environ.setdefault(_k, _v)
    if _fetched:
        logger.info("Loaded %d secret(s) from GCP Secret Manager", len(_fetched))


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./stock_research.db"
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    ENCRYPTION_KEY: str = ""  # Fernet key for IB key encryption
    SECRET_KEY: str = ""      # For cookie signing (itsdangerous)
    FRONTEND_URL: str = "http://localhost:5173"
    ALLOWED_ORIGINS: str = ""  # Comma-separated extra CORS origins
    SESSION_TIMEOUT_MINUTES: int = 60

    # GCP project ID — enables Secret Manager loading above
    GCP_PROJECT_ID: str = ""

    # SMTP settings — credentials should come from env / Secret Manager
    SMTP_HOST: str = "mail.spacemail.com"
    SMTP_PORT: int = 465
    SMTP_USER: str = "rahul@finoagent.ai"
    SMTP_PASS: str = ""          # ← Never hardcode; set via env/Secret Manager
    SMTP_FROM: str = "rahul@finoagent.ai"
    SMTP_USE_TLS: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
