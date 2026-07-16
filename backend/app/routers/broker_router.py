"""Broker integration router — connection management, contract search, order placement."""

import asyncio
import datetime
import json
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, dh
from cryptography.hazmat.backends import default_backend
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_user_api_key, encrypt_value
from ..database import get_db
from ..models import User, UserApiKey, BrokerConnection, BrokerOrder
from ..services.broker.base import OrderRequest, OrderResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/broker", tags=["broker"])

# IB credential key names stored in UserApiKey table
IB_CREDENTIAL_KEYS = [
    "ib_account_id",
    "ib_consumer_key",
    "ib_access_token",
    "ib_access_token_secret",
    "ib_encryption_key",
    "ib_signing_key",
]

IB_OPTIONAL_KEYS = ["ib_dh_prime"]


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class BrokerConnectionIn(BaseModel):
    """Simplified schema — user only enters 4 fields. Keys are pre-generated."""
    broker_type: str = Field(default="interactive_brokers")
    account_id: str = Field(..., min_length=1)
    consumer_key: str = Field(..., min_length=1)
    access_token: str = Field(..., min_length=1)
    access_token_secret: str = Field(..., min_length=1)


class BrokerConnectionOut(BaseModel):
    id: int
    broker_type: str
    account_id: str | None
    is_active: bool
    last_connected_at: str | None
    has_credentials: bool


class BrokerStatusOut(BaseModel):
    connected: bool
    authenticated: bool
    account_id: str | None = None
    server_name: str | None = None
    error: str | None = None


class ContractSearchIn(BaseModel):
    symbol: str
    sec_type: str = "OPT"
    expiry: str | None = None
    strike: float | None = None
    right: str | None = None  # "C" or "P"


class OrderLegIn(BaseModel):
    ticker: str
    action: str  # "Buy" or "Sell"
    type: str    # "Call" or "Put"
    strike: float
    qty: int     # Must be >= 1; IBKR rejects 0
    expiration: str
    limit_price: float

    @validator("qty", pre=True, always=True)
    def qty_at_least_one(cls, v):
        v = int(round(v)) if isinstance(v, float) else int(v)
        return max(1, v)


class PlaceStrategyIn(BaseModel):
    ticker: str
    strategy: str = "dual_direction_buffer"
    legs: list[OrderLegIn]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_ib_service(user: User, db: AsyncSession):
    """Load IB credentials and create IBService instance.

    IBService.__init__ → IbkrClient.__init__ → generate_live_session_token()
    makes a blocking HTTP POST to IBKR.  We run it in a thread pool so it
    doesn't block the async event loop.
    """
    from ..services.broker.ib_service import IBService

    creds = {}
    for key_name in IB_CREDENTIAL_KEYS:
        value = await get_user_api_key(db, user.id, key_name)
        if not value:
            raise HTTPException(
                status_code=404,
                detail=f"Missing IB credential: {key_name}. Configure Interactive Brokers in Settings.",
            )
        creds[key_name] = value

    # Optional keys
    dh_prime = await get_user_api_key(db, user.id, "ib_dh_prime")

    # IBService constructor makes a blocking OAuth handshake to IBKR —
    # run it in a thread to avoid blocking the event loop.
    def _build():
        return IBService(
            account_id=creds["ib_account_id"],
            consumer_key=creds["ib_consumer_key"],
            access_token=creds["ib_access_token"],
            access_token_secret=creds["ib_access_token_secret"],
            encryption_key=creds["ib_encryption_key"],
            signing_key=creds["ib_signing_key"],
            dh_prime=dh_prime,
        )

    loop = asyncio.get_event_loop()
    try:
        service = await loop.run_in_executor(None, _build)
    except Exception as exc:
        # Convert any IBKR auth/network error into a readable HTTPException
        _msg = _friendly_ib_error(str(exc))
        raise HTTPException(status_code=502, detail=_msg) from exc

    # Ensure no background tickler thread is left running.
    # ibind may start one during __init__ even with maintain_oauth=False.
    service.shutdown()

    return service


def _friendly_ib_error(raw: str) -> str:
    """Convert raw ibind/IBKR error strings into user-readable messages."""
    low = raw.lower()
    if "invalid consumer" in low:
        return (
            "IBKR rejected the credentials (invalid consumer). Common causes:\n"
            "1. Consumer Key mismatch — must exactly match the IBKR portal (9 chars, uppercase A-Z)\n"
            "2. Public keys mismatch — the .pem files uploaded to IBKR must be from Step 1 (signing_public_key.pem & encryption_public_key.pem). "
            "If you regenerated keys, re-upload the new public keys to IBKR and regenerate Access Token/Secret.\n"
            "3. DH params mismatch — make sure dhparam.pem from Step 1 was uploaded to IBKR.\n"
            "4. OAuth not enabled — toggle the OAuth switch ON in the IBKR portal.\n"
            "5. Account type — IBKR Pro account is required (not IBKR Lite)."
        )
    if "401" in raw or "unauthorized" in low:
        return (
            "IBKR returned 401 Unauthorized. Check that your Access Token, "
            "Access Token Secret, and Consumer Key are correct and that OAuth is enabled in the IBKR portal."
        )
    if "connection" in low or "timeout" in low or "network" in low:
        return "Could not reach IBKR servers. Check your network connection and try again."
    return f"IBKR connection error: {raw}"


async def _get_broker_connection(user: User, db: AsyncSession) -> BrokerConnection | None:
    result = await db.execute(
        select(BrokerConnection).where(
            BrokerConnection.user_id == user.id,
            BrokerConnection.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def _upsert_api_key(db: AsyncSession, user_id: int, key_name: str, value: str):
    """Save or update an encrypted API key."""
    encrypted = encrypt_value(value)
    result = await db.execute(
        select(UserApiKey).where(
            UserApiKey.user_id == user_id,
            UserApiKey.key_name == key_name,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.encrypted_value = encrypted
        existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        db.add(UserApiKey(
            user_id=user_id,
            key_name=key_name,
            encrypted_value=encrypted,
        ))


# ---------------------------------------------------------------------------
# Key Generation
# ---------------------------------------------------------------------------

@router.post("/generate-keys")
async def generate_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate RSA signature/encryption key pairs and DH parameters.

    Returns public keys + DH params for the user to paste into IBKR's
    self-service portal. Private keys are stored encrypted automatically.
    """
    # Generate RSA 2048 signature key pair
    sig_private = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    sig_private_pem = sig_private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    sig_public_pem = sig_private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    # Generate RSA 2048 encryption key pair
    enc_private = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    enc_private_pem = enc_private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    enc_public_pem = enc_private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    # Generate DH parameters (use standard 2048-bit prime for speed)
    dh_params = dh.generate_parameters(generator=2, key_size=2048, backend=default_backend())
    dh_pem = dh_params.parameter_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.ParameterFormat.PKCS3,
    ).decode()

    # Extract DH prime as hex
    dh_numbers = dh_params.parameter_numbers()
    dh_prime_hex = format(dh_numbers.p, 'x')

    # Store private keys encrypted
    await _upsert_api_key(db, user.id, "ib_signing_key", sig_private_pem)
    await _upsert_api_key(db, user.id, "ib_encryption_key", enc_private_pem)
    await _upsert_api_key(db, user.id, "ib_dh_prime", dh_prime_hex)
    await db.commit()

    return {
        "signature_public_key": sig_public_pem,
        "encryption_public_key": enc_public_pem,
        "dh_params": dh_pem,
        "dh_prime_hex": dh_prime_hex,
        "keys_stored": True,
        "message": "Keys generated. Copy the public keys and DH params into your IBKR portal.",
    }


# ---------------------------------------------------------------------------
# Connection Management
# ---------------------------------------------------------------------------

@router.post("/diagnose")
async def diagnose_connection(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run step-by-step diagnostic checks on IBKR OAuth credentials.

    Tests each component independently to pinpoint exactly where authentication
    fails, rather than getting a single opaque error.
    """
    import base64
    import traceback

    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5 as PKCS1_v1_5_Cipher

    results = {
        "step1_credentials_present": {},
        "step2_key_parsing": {},
        "step3_access_token_secret_decrypt": {},
        "step4_dh_prime": {},
        "step5_oauth_handshake": {},
    }

    # ── Step 1: Check all credentials exist ──
    creds = {}
    all_present = True
    for key_name in IB_CREDENTIAL_KEYS + IB_OPTIONAL_KEYS:
        value = await get_user_api_key(db, user.id, key_name)
        present = value is not None and len(value) > 0
        if key_name in ("ib_access_token", "ib_access_token_secret", "ib_consumer_key"):
            masked = f"{value[:4]}...{value[-4:]}" if value and len(value) > 8 else ("***" if value else None)
        elif key_name == "ib_account_id":
            masked = value  # account ID isn't secret
        elif key_name == "ib_dh_prime":
            masked = f"{value[:8]}...({len(value)} hex chars)" if value else None
        else:
            masked = f"PEM ({len(value)} chars)" if value else None
        results["step1_credentials_present"][key_name] = {
            "present": present,
            "preview": masked,
        }
        if present:
            creds[key_name] = value
        elif key_name in IB_CREDENTIAL_KEYS:
            all_present = False

    if not all_present:
        results["summary"] = "FAIL: Missing required credentials. Complete Steps 1-3 of the setup wizard."
        return results

    # ── Step 2: Parse private keys ──
    try:
        sig_key = RSA.importKey(creds["ib_signing_key"])
        results["step2_key_parsing"]["signing_key"] = {
            "ok": True,
            "bits": sig_key.size_in_bits(),
            "has_private": sig_key.has_private(),
        }
    except Exception as e:
        results["step2_key_parsing"]["signing_key"] = {"ok": False, "error": str(e)}

    try:
        enc_key = RSA.importKey(creds["ib_encryption_key"])
        results["step2_key_parsing"]["encryption_key"] = {
            "ok": True,
            "bits": enc_key.size_in_bits(),
            "has_private": enc_key.has_private(),
        }
    except Exception as e:
        results["step2_key_parsing"]["encryption_key"] = {"ok": False, "error": str(e)}

    if not results["step2_key_parsing"].get("signing_key", {}).get("ok") or \
       not results["step2_key_parsing"].get("encryption_key", {}).get("ok"):
        results["summary"] = "FAIL: Cannot parse stored private keys. Regenerate keys in Step 1."
        return results

    # ── Step 3: Decrypt access_token_secret ──
    try:
        ats_bytes = base64.b64decode(creds["ib_access_token_secret"])
        results["step3_access_token_secret_decrypt"]["base64_decode"] = {
            "ok": True,
            "decoded_length": len(ats_bytes),
        }
    except Exception as e:
        results["step3_access_token_secret_decrypt"]["base64_decode"] = {
            "ok": False,
            "error": f"access_token_secret is not valid base64: {e}",
        }
        results["summary"] = (
            "FAIL: access_token_secret is not valid base64. "
            "Copy the exact value from the IBKR portal."
        )
        return results

    try:
        cipher = PKCS1_v1_5_Cipher.new(enc_key)
        decrypted = cipher.decrypt(ats_bytes, None)
        if decrypted is None:
            results["step3_access_token_secret_decrypt"]["rsa_decrypt"] = {
                "ok": False,
                "error": (
                    "RSA decryption returned None — the access_token_secret was NOT encrypted "
                    "with the current encryption public key. This means either:\n"
                    "1. You generated a NEW set of keys (Step 1) AFTER creating the access token in IBKR. "
                    "Fix: Upload the new public keys to IBKR, then regenerate the access token.\n"
                    "2. The access_token_secret was copied incorrectly."
                ),
            }
            results["summary"] = (
                "FAIL: access_token_secret cannot be decrypted. "
                "The access token must be generated AFTER uploading the current public keys to IBKR. "
                "If you regenerated keys, re-upload public keys and regenerate the access token in IBKR."
            )
            return results
        else:
            results["step3_access_token_secret_decrypt"]["rsa_decrypt"] = {
                "ok": True,
                "decrypted_length": len(decrypted),
                "prepend_hex_preview": decrypted.hex()[:16] + "...",
            }
    except Exception as e:
        results["step3_access_token_secret_decrypt"]["rsa_decrypt"] = {
            "ok": False,
            "error": str(e),
        }
        results["summary"] = f"FAIL: RSA decryption error: {e}"
        return results

    # ── Step 4: DH prime ──
    dh_prime = creds.get("ib_dh_prime")
    if dh_prime:
        try:
            p = int(dh_prime, 16)
            results["step4_dh_prime"] = {
                "ok": True,
                "bit_length": p.bit_length(),
            }
        except ValueError as e:
            results["step4_dh_prime"] = {"ok": False, "error": f"Invalid hex: {e}"}
    else:
        results["step4_dh_prime"] = {"ok": False, "error": "dh_prime not stored"}

    # ── Step 5: Attempt OAuth handshake ──
    try:
        from ..services.broker.ib_service import IBService

        def _build():
            return IBService(
                account_id=creds["ib_account_id"],
                consumer_key=creds["ib_consumer_key"],
                access_token=creds["ib_access_token"],
                access_token_secret=creds["ib_access_token_secret"],
                encryption_key=creds["ib_encryption_key"],
                signing_key=creds["ib_signing_key"],
                dh_prime=dh_prime,
            )

        loop = asyncio.get_event_loop()
        service = await loop.run_in_executor(None, _build)
        service.shutdown()  # Don't leave tickler threads running
        results["step5_oauth_handshake"] = {"ok": True, "message": "Live session token acquired!"}
        results["summary"] = "ALL CHECKS PASSED — IBKR connection is working."
    except Exception as exc:
        tb = traceback.format_exception(exc)
        raw = str(exc)
        results["step5_oauth_handshake"] = {
            "ok": False,
            "error": raw,
            "traceback_tail": "".join(tb[-5:]),
        }

        # Provide targeted advice based on error
        low = raw.lower()
        if "invalid consumer" in low:
            results["summary"] = (
                "FAIL at OAuth handshake: IBKR says 'invalid consumer'. Steps 1-4 passed, "
                "so your keys and secrets are valid locally. The issue is on IBKR's side:\n"
                "1. Verify the Consumer Key in IBKR portal matches EXACTLY (case-sensitive).\n"
                "2. Make sure OAuth is toggled ON in the IBKR portal.\n"
                "3. IBKR Pro account is required (not IBKR Lite).\n"
                "4. If you just created the consumer, wait 15-30 minutes for IBKR to propagate.\n"
                "5. Try the Paper Trading account first to verify the flow works."
            )
        elif "decrypt" in low or "padding" in low:
            results["summary"] = (
                "FAIL: Decryption error during OAuth handshake. "
                "Re-upload public keys to IBKR and regenerate the access token."
            )
        else:
            results["summary"] = f"FAIL at OAuth handshake: {_friendly_ib_error(raw)}"

    return results


@router.get("/connection")
async def get_connection(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conn = await _get_broker_connection(user, db)
    if not conn:
        return {"configured": False}

    # Check if all credentials exist
    has_creds = True
    for key_name in IB_CREDENTIAL_KEYS:
        val = await get_user_api_key(db, user.id, key_name)
        if not val:
            has_creds = False
            break

    # Check if keys have been generated
    has_keys = bool(await get_user_api_key(db, user.id, "ib_signing_key"))

    return {
        "id": conn.id,
        "broker_type": conn.broker_type,
        "account_id": conn.account_id,
        "is_active": conn.is_active,
        "last_connected_at": str(conn.last_connected_at) if conn.last_connected_at else None,
        "has_credentials": has_creds,
        "has_keys": has_keys,
    }


@router.post("/connection")
async def save_connection(
    body: BrokerConnectionIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify that keys were pre-generated
    signing_key = await get_user_api_key(db, user.id, "ib_signing_key")
    encryption_key = await get_user_api_key(db, user.id, "ib_encryption_key")
    if not signing_key or not encryption_key:
        raise HTTPException(
            status_code=400,
            detail="Generate keys first (Step 1) before saving credentials.",
        )

    # Save user-provided credentials
    cred_map = {
        "ib_account_id": body.account_id,
        "ib_consumer_key": body.consumer_key,
        "ib_access_token": body.access_token,
        "ib_access_token_secret": body.access_token_secret,
    }
    for key_name, value in cred_map.items():
        await _upsert_api_key(db, user.id, key_name, value)

    # Upsert BrokerConnection record
    conn = await _get_broker_connection(user, db)
    if conn:
        conn.broker_type = body.broker_type
        conn.account_id = body.account_id
        conn.updated_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        conn = BrokerConnection(
            user_id=user.id,
            broker_type=body.broker_type,
            account_id=body.account_id,
            is_active=True,
        )
        db.add(conn)

    await db.commit()
    await db.refresh(conn)

    return {
        "success": True,
        "message": "Interactive Brokers connection saved.",
        "connection_id": conn.id,
    }


@router.delete("/connection")
async def delete_connection(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Delete all IB credential keys
    for key_name in IB_CREDENTIAL_KEYS + IB_OPTIONAL_KEYS:
        result = await db.execute(
            select(UserApiKey).where(
                UserApiKey.user_id == user.id,
                UserApiKey.key_name == key_name,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            await db.delete(existing)

    # Deactivate connection
    conn = await _get_broker_connection(user, db)
    if conn:
        await db.delete(conn)

    await db.commit()
    return {"success": True, "message": "Broker connection removed."}


# ---------------------------------------------------------------------------
# Status & Accounts
# ---------------------------------------------------------------------------

@router.get("/status", response_model=BrokerStatusOut)
async def check_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        service = await _get_ib_service(user, db)
    except HTTPException as exc:
        # 404 = credentials not configured; 502 = IBKR auth/network error
        return BrokerStatusOut(connected=False, authenticated=False, error=exc.detail)

    status = await service.check_connection()

    # Update last_connected_at if connected
    if status.connected:
        conn = await _get_broker_connection(user, db)
        if conn:
            conn.last_connected_at = datetime.datetime.now(datetime.timezone.utc)
            await db.commit()

    return BrokerStatusOut(
        connected=status.connected,
        authenticated=status.authenticated,
        account_id=status.account_id,
        server_name=status.server_name,
        error=status.error,
    )


@router.get("/accounts")
async def get_accounts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        service = await _get_ib_service(user, db)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    accounts = await service.get_accounts()
    return {"accounts": accounts}


# ---------------------------------------------------------------------------
# Contract Search
# ---------------------------------------------------------------------------

@router.post("/contract/search")
async def search_contract(
    body: ContractSearchIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        service = await _get_ib_service(user, db)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    contracts = await service.search_contract(
        symbol=body.symbol,
        sec_type=body.sec_type,
        expiry=body.expiry,
        strike=body.strike,
        right=body.right,
    )

    if not contracts:
        raise HTTPException(
            status_code=404,
            detail=f"No contracts found for {body.symbol} {body.sec_type} strike={body.strike} right={body.right} expiry={body.expiry}",
        )

    return {
        "contracts": [
            {
                "conid": c.conid,
                "symbol": c.symbol,
                "sec_type": c.sec_type,
                "exchange": c.exchange,
                "expiry": c.expiry,
                "strike": c.strike,
                "right": c.right,
                "description": c.description,
            }
            for c in contracts
        ]
    }


# ---------------------------------------------------------------------------
# Order Preview
# ---------------------------------------------------------------------------

class PreviewLegIn(BaseModel):
    ticker: str
    action: str
    type: str
    strike: float
    qty: int     # Must be >= 1; IBKR rejects 0
    expiration: str
    limit_price: float
    conid: int | None = None  # If known from IBKR quote provider

    @validator("qty", pre=True, always=True)
    def qty_at_least_one(cls, v):
        v = int(round(v)) if isinstance(v, float) else int(v)
        return max(1, v)


class PreviewStrategyIn(BaseModel):
    ticker: str
    strategy: str = "box_spread"
    legs: list[PreviewLegIn]


@router.post("/order/preview")
async def preview_strategy_order(
    body: PreviewStrategyIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Preview a multi-leg strategy order via IBKR.

    Resolves contracts and returns estimated fills, commissions, and margin
    impact *without* submitting orders.
    """
    try:
        service = await _get_ib_service(user, db)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    conn = await _get_broker_connection(user, db)
    if not conn:
        raise HTTPException(status_code=404, detail="No active broker connection.")

    account_id = conn.account_id or ""
    leg_previews = []

    for leg in body.legs:
        try:
            # Resolve contract conid
            if leg.conid:
                conid = leg.conid
            else:
                right = "C" if leg.type == "Call" else "P"
                contracts = await service.search_contract(
                    symbol=leg.ticker,
                    sec_type="OPT",
                    expiry=leg.expiration,
                    strike=leg.strike,
                    right=right,
                )
                if not contracts:
                    leg_previews.append({
                        "strike": leg.strike,
                        "type": leg.type,
                        "action": leg.action,
                        "resolved": False,
                        "error": f"Contract not found: {leg.ticker} {leg.strike} {leg.type} {leg.expiration}",
                    })
                    continue
                conid = contracts[0].conid

            # Try whatif / preview order
            side = "BUY" if leg.action == "Buy" else "SELL"
            preview_data = await service.preview_order(
                account_id=account_id,
                conid=conid,
                side=side,
                quantity=leg.qty,
                price=leg.limit_price,
            )

            leg_previews.append({
                "strike": leg.strike,
                "type": leg.type,
                "action": leg.action,
                "conid": conid,
                "resolved": True,
                "limit_price": leg.limit_price,
                "estimated_commission": preview_data.get("commission"),
                "margin_impact": preview_data.get("margin_impact"),
                "warnings": preview_data.get("warnings", []),
                "preview_data": preview_data,
            })
        except Exception as e:
            logger.warning(f"Preview failed for leg {leg.strike} {leg.type}: {e}")
            leg_previews.append({
                "strike": leg.strike,
                "type": leg.type,
                "action": leg.action,
                "resolved": False,
                "error": str(e),
            })

    # Compute totals
    total_commission = sum(
        p.get("estimated_commission", 0) or 0 for p in leg_previews if p.get("resolved")
    )
    all_resolved = all(p.get("resolved") for p in leg_previews)
    all_warnings = []
    for p in leg_previews:
        w = p.get("warnings", [])
        if isinstance(w, str):
            all_warnings.append(w)
        elif isinstance(w, list):
            all_warnings.extend(w)


    return {
        "preview_success": all_resolved,
        "legs": leg_previews,
        "total_estimated_commission": round(total_commission, 2),
        "warnings": list(set(all_warnings)),
        "account_id": account_id,
    }


# ---------------------------------------------------------------------------
# Order Placement
# ---------------------------------------------------------------------------

@router.post("/order/place-strategy")
async def place_strategy_order(
    body: PlaceStrategyIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        service = await _get_ib_service(user, db)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    conn = await _get_broker_connection(user, db)
    if not conn:
        raise HTTPException(status_code=404, detail="No active broker connection.")

    account_id = conn.account_id or ""

    # Create audit record
    order_record = BrokerOrder(
        user_id=user.id,
        broker_connection_id=conn.id,
        ticker=body.ticker,
        strategy=body.strategy,
        order_data=json.dumps([leg.model_dump() for leg in body.legs]),
        status="submitted",
    )
    db.add(order_record)
    await db.commit()
    await db.refresh(order_record)

    # --- Phase 1: Resolve all contracts first ---
    resolved_legs = []
    leg_results = []
    resolution_failed = False

    for leg in body.legs:
        try:
            right = "C" if leg.type == "Call" else "P"
            contracts = await service.search_contract(
                symbol=leg.ticker,
                sec_type="OPT",
                expiry=leg.expiration,
                strike=leg.strike,
                right=right,
            )

            if not contracts:
                leg_results.append({
                    "success": False,
                    "error": f"Contract not found: {leg.ticker} {leg.strike} {leg.type} {leg.expiration}",
                })
                resolution_failed = True
                continue

            conid = contracts[0].conid
            side = "BUY" if leg.action == "Buy" else "SELL"
            order = OrderRequest(
                conid=conid,
                side=side,
                quantity=leg.qty,
                order_type="LMT",
                price=leg.limit_price,
                tif="DAY",
            )
            resolved_legs.append((order, leg))
            leg_results.append(None)  # placeholder for result

        except Exception as e:
            logger.error(f"Failed to resolve leg {leg.ticker} {leg.strike} {leg.type}: {e}")
            leg_results.append({
                "success": False,
                "error": str(e),
            })
            resolution_failed = True

    # --- Phase 2: Submit all resolved legs as a single multi-leg order ---
    broker_order_ids = []

    if resolved_legs:
        try:
            order_requests = [ol[0] for ol in resolved_legs]
            multi_results = await service.place_multi_order(account_id, order_requests)

            # Map results back to the leg_results list
            resolved_idx = 0
            for i, lr in enumerate(leg_results):
                if lr is None:  # this was a resolved leg
                    r = multi_results[resolved_idx] if resolved_idx < len(multi_results) else OrderResult(success=False, error="No response")
                    leg_results[i] = {
                        "success": r.success,
                        "order_id": r.order_id,
                        "message": r.message,
                        "error": r.error,
                    }
                    if r.order_id:
                        broker_order_ids.append(r.order_id)
                    resolved_idx += 1
        except Exception as e:
            logger.error(f"Multi-leg order submission failed: {e}")
            for i, lr in enumerate(leg_results):
                if lr is None:
                    leg_results[i] = {"success": False, "error": str(e)}

    # Update order record
    all_success = all(r.get("success", False) for r in leg_results if r)
    any_success = any(r.get("success", False) for r in leg_results if r)

    order_record.status = "filled" if all_success else ("partial" if any_success else "error")
    order_record.broker_order_ids = json.dumps(broker_order_ids) if broker_order_ids else None
    if not all_success:
        errors = [r.get("error", "") for r in leg_results if r and r.get("error")]
        order_record.error_message = "; ".join(errors)

    await db.commit()

    return {
        "overall_success": all_success,
        "legs": [r for r in leg_results if r],
        "order_record_id": order_record.id,
    }


# ---------------------------------------------------------------------------
# Single Option Quote
# ---------------------------------------------------------------------------

class OptionQuoteIn(BaseModel):
    ticker: str
    expiration: str          # YYYY-MM-DD
    strike: float
    right: str               # "C" or "P"
    quote_source: str = "yfinance"


@router.post("/option-quote")
async def get_option_quote(
    body: OptionQuoteIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch bid/ask/mid for a single option contract.

    For IBKR: uses a fast single-contract lookup (3 API calls) instead of
    fetching the entire option chain.
    For yfinance: fetches the chain and finds the closest match.
    """
    from ..services.quote_providers import get_provider

    try:
        if body.quote_source == "ibkr":
            provider = get_provider("ibkr", user=user, db=db)
        else:
            provider = get_provider("yfinance")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    right = body.right.upper()

    # Fast path for IBKR: single-contract lookup
    if body.quote_source == "ibkr" and hasattr(provider, "get_single_option_quote"):
        try:
            quote = await provider.get_single_option_quote(
                body.ticker, body.expiration, body.strike, right
            )
            if quote:
                return {
                    "strike": quote.strike,
                    "right": quote.right,
                    "expiration": quote.expiration,
                    "bid": round(quote.bid, 2),
                    "ask": round(quote.ask, 2),
                    "mid": round(quote.mid, 2),
                    "last": round(quote.last, 2),
                }
        except Exception as exc:
            logger.warning(f"IBKR single-quote fast path failed, falling back to chain: {exc}")

    # Fallback: fetch full chain and find closest match
    try:
        chain = await provider.get_option_chain(body.ticker, body.expiration)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch option chain: {exc}")

    candidates = [q for q in chain.quotes if q.right == right]
    if not candidates:
        raise HTTPException(status_code=404, detail=f"No {right} options found for {body.ticker} exp {body.expiration}")

    best = min(candidates, key=lambda q: abs(q.strike - body.strike))
    return {
        "strike": best.strike,
        "right": best.right,
        "expiration": best.expiration,
        "bid": round(best.bid, 2),
        "ask": round(best.ask, 2),
        "mid": round(best.mid, 2),
        "last": round(best.last, 2),
    }


@router.post("/option-quotes-batch")
async def get_option_quotes_batch(
    requests: list[OptionQuoteIn],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch bid/ask/mid for MULTIPLE option contracts.

    For IBKR: uses get_multiple_option_quotes to heavily batch resolving conids
    and performing live market data snapshots in one API call.
    For yfinance: falls back to making sequential fetches or caching the chain internally.
    """
    from ..services.quote_providers import get_provider

    if not requests:
        return []

    # Group requests by source
    grouped = {}
    for req in requests:
        src = req.quote_source or "yfinance"
        grouped.setdefault(src, []).append(req)

    results = [None] * len(requests)
    
    # Process each source
    for current_source, source_reqs in grouped.items():
        try:
            if current_source == "ibkr":
                provider = get_provider("ibkr", user=user, db=db)
            else:
                provider = get_provider("yfinance")
        except Exception as exc:
            for req in source_reqs:
                idx = requests.index(req)
                results[idx] = {"error": str(exc)}
            continue

        if current_source == "ibkr" and hasattr(provider, "get_multiple_option_quotes"):
            # Use the highly optimized batch path for IBKR
            # Need to map specific source_reqs index to original requests index
            req_dicts = [
                {
                    "symbol": req.ticker,
                    "expiration": req.expiration,
                    "strike": req.strike,
                    "right": req.right,
                }
                for req in source_reqs
            ]
            try:
                ibkr_quotes = await provider.get_multiple_option_quotes(req_dicts)
                for q, req in zip(ibkr_quotes, source_reqs):
                    idx = requests.index(req)
                    if q:
                        results[idx] = {
                            "strike": q.strike,
                            "right": q.right,
                            "expiration": q.expiration,
                            "bid": round(q.bid, 2),
                            "ask": round(q.ask, 2),
                            "mid": round(q.mid, 2),
                            "last": round(q.last, 2),
                            "iv": q.iv or 0,
                            "oi": q.oi or 0,
                            "volume": q.volume or 0,
                        }
                    else:
                        results[idx] = {"error": "Contract not found or snapshot failed."}
                continue
            except Exception as exc:
                logger.warning(f"Batch fetch failed for IBKR: {exc}")
                # Fall back to sequential if batch fails
                pass

        # Fallback or yfinance handling: Fetch Sequentially internally
        # We can optimize this by caching chains fetched in this request
        chain_cache = {}
        for req in source_reqs:
            idx = requests.index(req)
            right = req.right.upper()
            cache_key = f"{req.ticker}_{req.expiration}_{current_source}"
            
            try:
                # Fast path single quote for IBKR fallback
                if current_source == "ibkr" and hasattr(provider, "get_single_option_quote"):
                    q = await provider.get_single_option_quote(req.ticker, req.expiration, req.strike, right)
                    if q:
                        results[idx] = {
                            "strike": q.strike, "right": q.right, "expiration": q.expiration,
                            "bid": round(q.bid, 2), "ask": round(q.ask, 2), "mid": round(q.mid, 2), 
                            "last": round(q.last, 2), "iv": q.iv or 0, "oi": q.oi or 0, "volume": q.volume or 0,
                        }
                        continue

                # Standard Chain Fallback
                if cache_key not in chain_cache:
                    chain_cache[cache_key] = await provider.get_option_chain(req.ticker, req.expiration)
                
                chain = chain_cache[cache_key]
                candidates = [q for q in chain.quotes if q.right == right]
                if not candidates:
                    results[idx] = {"error": f"No {right} options found"}
                    continue
                
                best = min(candidates, key=lambda q: abs(q.strike - req.strike))
                results[idx] = {
                    "strike": best.strike, "right": best.right, "expiration": best.expiration,
                    "bid": round(best.bid, 2), "ask": round(best.ask, 2), "mid": round(best.mid, 2), 
                    "last": round(best.last, 2), "iv": best.iv or 0, "oi": best.oi or 0, "volume": best.volume or 0,
                }
            except Exception as exc:
                results[idx] = {"error": str(exc)}

    return results

    closest = min(candidates, key=lambda q: abs(q.strike - body.strike))

    return {
        "strike": closest.strike,
        "right": closest.right,
        "expiration": closest.expiration,
        "bid": round(closest.bid, 2),
        "ask": round(closest.ask, 2),
        "mid": round(closest.mid, 2),
        "last": round(closest.last, 2),
    }


# ---------------------------------------------------------------------------
# Strategy Computation (IBKR mode)
# ---------------------------------------------------------------------------

class DualDirectionBufferIn(BaseModel):
    ticker: str
    amount: float
    duration_days: int
    downside_buffer_pct: float
    upside_cap_pct: float
    target_expiration: str | None = None


@router.post("/strategy/dual-direction-buffer")
async def compute_dual_direction_buffer_ibkr(
    body: DualDirectionBufferIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compute Dual Direction Buffer strategy with IBKR-compatible integer quantities.

    Uses yfinance data for strike/expiry identification and rounds all contract
    quantities to whole numbers (math.ceil) for IBKR order placement.
    Returns bid/ask for each leg alongside mid prices so the frontend can
    offer pricing-mode controls (bid / mid / ask / smart / custom).
    """
    from ..services.dual_direction_service import run_dual_direction_buffer_ibkr

    # Verify broker is configured (credentials present)
    try:
        await _get_ib_service(user, db)
    except HTTPException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    result = await run_dual_direction_buffer_ibkr(
        ticker=body.ticker,
        amount=body.amount,
        duration_days=body.duration_days,
        downside_buffer_pct=body.downside_buffer_pct,
        upside_cap_pct=body.upside_cap_pct,
        target_expiration=body.target_expiration,
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "Failed to compute Dual Direction Buffer strategy"),
        )

    return result


# ---------------------------------------------------------------------------
# Order History
# ---------------------------------------------------------------------------

@router.get("/orders")
async def get_orders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BrokerOrder)
        .where(BrokerOrder.user_id == user.id)
        .order_by(BrokerOrder.created_at.desc())
        .limit(50)
    )
    orders = result.scalars().all()

    return {
        "orders": [
            {
                "id": o.id,
                "ticker": o.ticker,
                "strategy": o.strategy,
                "status": o.status,
                "order_data": json.loads(o.order_data) if o.order_data else [],
                "broker_order_ids": json.loads(o.broker_order_ids) if o.broker_order_ids else [],
                "error_message": o.error_message,
                "created_at": str(o.created_at),
            }
            for o in orders
        ]
    }
