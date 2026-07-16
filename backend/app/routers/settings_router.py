"""Settings routes: manage user API keys and preferences."""

import datetime
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import decrypt_value, encrypt_value, get_admin_user, get_current_user
from ..database import get_db
from ..models import User, UserApiKey, AllowedUser
from ..services.llm_service import canonical_model

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Available LLM models (cheapest/free first).
# NOTE: keep the Gemini IDs current — retired IDs (1.5.x, 2.0-flash) return HTTP 404.
# Retired selections are auto-upgraded by ``canonical_model`` on read + at call time.
ALL_MODELS = [
    {"id": "gpt-4o-mini", "label": "GPT-4o Mini (OpenAI - cheapest)"},
    {"id": "gpt-4o", "label": "GPT-4o (OpenAI)"},
    {"id": "gpt-4-turbo", "label": "GPT-4 Turbo (OpenAI)"},
    {"id": "o3-mini", "label": "o3-mini (OpenAI reasoning)"},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash (Google - free tier)"},
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro (Google)"},
    {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash (Google - latest)"},
    {"id": "gemini-3.1-flash-lite", "label": "Gemini 3.1 Flash-Lite (Google - fast & cheap)"},
]
DEFAULT_MODEL = "gpt-4o-mini"


# --------------------------------------------------------------------------
# Pydantic schemas
# --------------------------------------------------------------------------

class ApiKeyIn(BaseModel):
    key_name: str
    value: str


class ApiKeyOut(BaseModel):
    key_name: str
    masked_value: str  # last 4 chars only
    updated_at: datetime.datetime | None = None


class AllowedUserIn(BaseModel):
    email: str

class AllowedUserOut(BaseModel):
    id: int
    email: str
    is_premium: bool = False
    created_at: datetime.datetime | None = None

class PremiumToggleIn(BaseModel):
    is_premium: bool


# --------------------------------------------------------------------------
# Admin Dependency — imported from auth so the definition is centralised
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# GET /api/settings/keys — list saved key names + masked values
# --------------------------------------------------------------------------
@router.get("/keys", response_model=list[ApiKeyOut])
async def list_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserApiKey).where(UserApiKey.user_id == user.id)
    )
    keys = result.scalars().all()

    # Exclude preference entries (like openai_model) — only return actual API keys
    PREFERENCE_KEYS = {"openai_model", "email_notifications", "show_ai_sections"}

    out: list[ApiKeyOut] = []
    for k in keys:
        if k.key_name in PREFERENCE_KEYS:
            continue
        try:
            plain = decrypt_value(k.encrypted_value)
            masked = "***" + plain[-4:] if len(plain) >= 4 else "***"
        except Exception:
            masked = "***"
        out.append(ApiKeyOut(key_name=k.key_name, masked_value=masked, updated_at=k.updated_at))
    return out


# --------------------------------------------------------------------------
# POST /api/settings/keys — save or update an API key
# --------------------------------------------------------------------------
@router.post("/keys", response_model=ApiKeyOut)
async def save_key(
    body: ApiKeyIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.key_name or not body.value:
        raise HTTPException(status_code=400, detail="key_name and value are required")

    encrypted = encrypt_value(body.value)

    result = await db.execute(
        select(UserApiKey).where(
            UserApiKey.user_id == user.id,
            UserApiKey.key_name == body.key_name,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_value = encrypted
        existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
        await db.commit()
        await db.refresh(existing)
        updated_at = existing.updated_at
    else:
        new_key = UserApiKey(
            user_id=user.id,
            key_name=body.key_name,
            encrypted_value=encrypted,
        )
        db.add(new_key)
        await db.commit()
        await db.refresh(new_key)
        updated_at = new_key.updated_at

    masked = "***" + body.value[-4:] if len(body.value) >= 4 else "***"
    return ApiKeyOut(key_name=body.key_name, masked_value=masked, updated_at=updated_at)


# --------------------------------------------------------------------------
# DELETE /api/settings/keys/{key_name} — remove an API key
# --------------------------------------------------------------------------
@router.delete("/keys/{key_name}")
async def delete_key(
    key_name: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserApiKey).where(
            UserApiKey.user_id == user.id,
            UserApiKey.key_name == key_name,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="API key not found")

    await db.delete(existing)
    await db.commit()
    return {"ok": True, "deleted": key_name}


# --------------------------------------------------------------------------
# GET /api/settings/models — list available LLM models
# --------------------------------------------------------------------------
@router.get("/models")
async def list_models():
    return {"models": ALL_MODELS, "default": DEFAULT_MODEL}


# --------------------------------------------------------------------------
# GET /api/settings/model — get user's selected model
# --------------------------------------------------------------------------
@router.get("/model")
async def get_model(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserApiKey).where(
            UserApiKey.user_id == user.id,
            UserApiKey.key_name == "openai_model",
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        try:
            model_id = decrypt_value(existing.encrypted_value)
        except Exception:
            model_id = DEFAULT_MODEL
    else:
        model_id = DEFAULT_MODEL
    # Upgrade a persisted-but-retired Gemini ID so the dropdown shows a live model.
    model_id = canonical_model(model_id) or DEFAULT_MODEL
    return {"model": model_id}


# --------------------------------------------------------------------------
# PUT /api/settings/model — set user's selected model
# --------------------------------------------------------------------------
class ModelIn(BaseModel):
    model: str


@router.put("/model")
async def set_model(
    body: ModelIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    valid_ids = {m["id"] for m in ALL_MODELS}
    if body.model not in valid_ids:
        raise HTTPException(400, f"Invalid model: {body.model}. Choose from: {', '.join(valid_ids)}")

    encrypted = encrypt_value(body.model)

    result = await db.execute(
        select(UserApiKey).where(
            UserApiKey.user_id == user.id,
            UserApiKey.key_name == "openai_model",
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_value = encrypted
        existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        db.add(UserApiKey(
            user_id=user.id,
            key_name="openai_model",
            encrypted_value=encrypted,
        ))

    await db.commit()
    return {"model": body.model}


# --------------------------------------------------------------------------
# GET /api/settings/email-notifications
# --------------------------------------------------------------------------
@router.get("/email-notifications")
async def get_email_notifications(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user's email notification preference."""
    result = await db.execute(
        select(UserApiKey).where(
            UserApiKey.user_id == user.id,
            UserApiKey.key_name == "email_notifications",
        )
    )
    existing = result.scalar_one_or_none()
    enabled = False
    if existing:
        try:
            enabled = decrypt_value(existing.encrypted_value) == "true"
        except Exception:
            pass
    return {"enabled": enabled, "email": user.email}


# --------------------------------------------------------------------------
# PUT /api/settings/email-notifications
# --------------------------------------------------------------------------
class EmailNotifIn(BaseModel):
    enabled: bool


@router.put("/email-notifications")
async def set_email_notifications(
    body: EmailNotifIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle email notifications for agent run reports."""
    encrypted = encrypt_value("true" if body.enabled else "false")

    result = await db.execute(
        select(UserApiKey).where(
            UserApiKey.user_id == user.id,
            UserApiKey.key_name == "email_notifications",
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_value = encrypted
        existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        db.add(UserApiKey(
            user_id=user.id,
            key_name="email_notifications",
            encrypted_value=encrypted,
        ))

    await db.commit()
    return {"enabled": body.enabled}


# --------------------------------------------------------------------------
# GET /api/settings/display-preferences
# --------------------------------------------------------------------------
@router.get("/display-preferences")
async def get_display_preferences(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-user dashboard display preferences.

    ``show_ai_sections`` gates the AI Impact / Fortress / Stress-Test blocks on the
    Fundamental tab — hidden by default (opt-in)."""
    result = await db.execute(
        select(UserApiKey).where(
            UserApiKey.user_id == user.id,
            UserApiKey.key_name == "show_ai_sections",
        )
    )
    existing = result.scalar_one_or_none()
    show_ai_sections = False
    if existing:
        try:
            show_ai_sections = decrypt_value(existing.encrypted_value) == "true"
        except Exception:
            pass
    return {"show_ai_sections": show_ai_sections}


# --------------------------------------------------------------------------
# PUT /api/settings/display-preferences
# --------------------------------------------------------------------------
class DisplayPrefsIn(BaseModel):
    show_ai_sections: bool


@router.put("/display-preferences")
async def set_display_preferences(
    body: DisplayPrefsIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Persist the AI-sections visibility toggle for this user."""
    encrypted = encrypt_value("true" if body.show_ai_sections else "false")

    result = await db.execute(
        select(UserApiKey).where(
            UserApiKey.user_id == user.id,
            UserApiKey.key_name == "show_ai_sections",
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_value = encrypted
        existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        db.add(UserApiKey(
            user_id=user.id,
            key_name="show_ai_sections",
            encrypted_value=encrypted,
        ))

    await db.commit()
    return {"show_ai_sections": body.show_ai_sections}


# --------------------------------------------------------------------------
# POST /api/settings/whatsapp
# --------------------------------------------------------------------------
class WhatsAppIn(BaseModel):
    whatsapp_number: str | None

@router.post("/whatsapp")
async def save_whatsapp(
    body: WhatsAppIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save or clear the user's WhatsApp number for the Channels integration."""
    # Simple cleanup of the number (e.g. removing spaces, dashes, or a leading 'whatsapp:' if they pasted it)
    clean_number = None
    if body.whatsapp_number:
        clean_number = body.whatsapp_number.replace(" ", "").replace("-", "").replace("whatsapp:", "").strip()
        
    user.whatsapp_number = clean_number
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return {"whatsapp_number": user.whatsapp_number}


# --------------------------------------------------------------------------
# Admin Allowlist Management
# --------------------------------------------------------------------------

@router.get("/allowlist", response_model=list[AllowedUserOut])
async def get_allowlist(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all allowed users (Admin only)"""
    result = await db.execute(select(AllowedUser).order_by(AllowedUser.created_at.desc()))
    users = result.scalars().all()
    return [{"id": u.id, "email": u.email, "is_premium": u.is_premium, "created_at": u.created_at} for u in users]


@router.post("/allowlist", response_model=AllowedUserOut)
async def add_allowed_user(
    body: AllowedUserIn,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Add a new allowed user (Admin only)"""
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    # Check if already exists
    result = await db.execute(select(AllowedUser).where(AllowedUser.email == email))
    existing = result.scalar_one_or_none()

    if existing:
        return AllowedUserOut(id=existing.id, email=existing.email, is_premium=existing.is_premium, created_at=existing.created_at)

    new_user = AllowedUser(email=email)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return AllowedUserOut(id=new_user.id, email=new_user.email, is_premium=new_user.is_premium, created_at=new_user.created_at)


@router.patch("/allowlist/{email}/premium", response_model=AllowedUserOut)
async def set_user_premium(
    email: str,
    body: PremiumToggleIn,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Toggle premium status for an allowed user (Admin only)"""
    email = email.lower().strip()
    result = await db.execute(select(AllowedUser).where(AllowedUser.email == email))
    existing = result.scalar_one_or_none()

    if not existing:
        raise HTTPException(status_code=404, detail="Email not found in allowlist")

    existing.is_premium = body.is_premium
    await db.commit()
    await db.refresh(existing)

    return AllowedUserOut(id=existing.id, email=existing.email, is_premium=existing.is_premium, created_at=existing.created_at)


@router.delete("/allowlist/{email}")
async def remove_allowed_user(
    email: str,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove an allowed user (Admin only)"""
    email = email.lower().strip()
    result = await db.execute(select(AllowedUser).where(AllowedUser.email == email))
    existing = result.scalar_one_or_none()

    if not existing:
        raise HTTPException(status_code=404, detail="Email not found in allowlist")

    await db.delete(existing)
    await db.commit()

    return {"ok": True, "deleted": email}
