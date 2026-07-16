"""Google OAuth setup and authentication utilities."""

import datetime
import uuid

from authlib.integrations.starlette_client import OAuth
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .models import AllowedUser, Session, User, UserApiKey

# ---------------------------------------------------------------------------
# Google OAuth client (authlib)
# ---------------------------------------------------------------------------
oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ---------------------------------------------------------------------------
# Cookie signer (itsdangerous)
# ---------------------------------------------------------------------------
_signer = URLSafeTimedSerializer(settings.SECRET_KEY)
COOKIE_NAME = "session_id"


def sign_session_id(session_id: str) -> str:
    """Return a signed token wrapping the raw session_id."""
    return _signer.dumps(session_id)


def unsign_session_id(token: str, max_age: int | None = None) -> str | None:
    """Unsign a cookie token and return the raw session_id, or None on failure."""
    try:
        return _signer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


# ---------------------------------------------------------------------------
# Fernet encryption for API keys
# ---------------------------------------------------------------------------
_fernet = Fernet(settings.ENCRYPTION_KEY.encode()) if settings.ENCRYPTION_KEY else None


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string and return the Fernet token as a string."""
    if _fernet is None:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(token: str) -> str:
    """Decrypt a Fernet token and return the plaintext."""
    if _fernet is None:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    return _fernet.decrypt(token.encode()).decode()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

async def create_session(
    db: AsyncSession,
    user_id: int,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create a new session row and return the raw session_id (UUID)."""
    session_id = str(uuid.uuid4())
    session = Session(
        session_id=session_id,
        user_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        last_activity=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(session)
    await db.commit()
    return session_id


async def validate_session(db: AsyncSession, raw_session_id: str) -> Session | None:
    """Return the Session row if it exists and has not timed out, else None."""
    result = await db.execute(select(Session).where(Session.session_id == raw_session_id))
    session = result.scalar_one_or_none()
    if session is None:
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    last = session.last_activity
    # Ensure last_activity is timezone-aware for comparison
    if last.tzinfo is None:
        last = last.replace(tzinfo=datetime.timezone.utc)

    elapsed = (now - last).total_seconds()
    if elapsed > settings.SESSION_TIMEOUT_MINUTES * 60:
        # Expired — delete it
        await db.delete(session)
        await db.commit()
        return None

    # Touch last_activity
    session.last_activity = now
    await db.commit()
    return session


# ---------------------------------------------------------------------------
# FastAPI dependency: get current user (raises 401 if not authenticated)
# ---------------------------------------------------------------------------

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency that extracts and validates the session cookie, returning the User."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    raw_session_id = unsign_session_id(token)
    if raw_session_id is None:
        raise HTTPException(status_code=401, detail="Invalid session cookie")

    session = await validate_session(db, raw_session_id)
    if session is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    result = await db.execute(select(User).where(User.id == session.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user


ADMIN_EMAIL = "karwa.rahul@gmail.com"


async def get_premium_user(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency — raises 403 for non-premium users. Admin is always premium."""
    if user.email == ADMIN_EMAIL:
        return user
    result = await db.execute(select(AllowedUser).where(AllowedUser.email == user.email))
    allowed = result.scalar_one_or_none()
    if not allowed or not allowed.is_premium:
        raise HTTPException(
            status_code=403,
            detail="This feature requires a Premium account. Please contact the admin to upgrade.",
        )
    return user


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency — raises 403 for non-admin users."""
    if user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ---------------------------------------------------------------------------
# Helper: get a user's decrypted API key by name
# ---------------------------------------------------------------------------

async def get_user_api_key(db: AsyncSession, user_id: int, key_name: str) -> str | None:
    """Return the decrypted API key value or None if not set.

    Two LLM-specific behaviours keep the app-wide model switch working:

    * Fetching ``openai_model`` or ``openai_api_key`` refreshes the request-scoped
      ``active_model`` context variable from the DB *every time* (never cached), so
      a single async context that serves multiple users — e.g. the scheduler — can
      never leak one user's model onto another's LLM calls.
    * When the selected model is a Gemini model, a request for ``openai_api_key``
      transparently returns the stored ``gemini_api_key`` so all existing call
      sites route to the right provider without changes.
    """
    from .services.llm_service import active_model

    async def _read(name: str) -> str | None:
        result = await db.execute(
            select(UserApiKey).where(
                UserApiKey.user_id == user_id,
                UserApiKey.key_name == name,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        try:
            return decrypt_value(row.encrypted_value)
        except Exception:
            return None

    if key_name in ("openai_api_key", "openai_model"):
        selected_model = await _read("openai_model")
        # Always refresh — this is what makes the model switch reliable per user.
        active_model.set(selected_model)

        if key_name == "openai_model":
            return selected_model

        # key_name == "openai_api_key": pick the provider key that matches the model.
        if selected_model and selected_model.startswith("gemini-"):
            return await _read("gemini_api_key")
        return await _read("openai_api_key")

    return await _read(key_name)
