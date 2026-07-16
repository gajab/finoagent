import logging
import httpx
from pydantic import BaseModel
from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..database import get_db
from ..models import User, Agent
from ..config import settings
from ..services.agent_service import execute_agent
from ..auth import get_user_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])

NODE_SERVICE_URL = "http://whatsapp-service:3000"

def send_whatsapp_message(to_number: str, body: str):
    """Helper to send a WhatsApp message via the internal Node.js service."""
    try:
        # We use httpx synchronously here or asynchronously.
        # BackgroundTasks can run sync functions, so this is fine, or we can use async.
        # Let's use httpx.post directly (sync) since it's a simple fire-and-forget in a background thread
        with httpx.Client() as client:
            resp = client.post(f"{NODE_SERVICE_URL}/send", json={
                "to": to_number,
                "message": body
            }, timeout=10.0)
            if resp.status_code == 200:
                logger.info(f"WhatsApp message sent to {to_number} via Node service")
            else:
                logger.error(f"Failed to send WhatsApp message: Node service returned {resp.status_code}")
    except Exception as e:
        logger.error(f"Error communicating with Node WhatsApp service: {e}")

async def run_agent_and_reply(agent_id: int, user_id: int, db: AsyncSession, to_whatsapp: str):
    """Background task to run the agent and send the result back via WhatsApp."""
    try:
        # We need the OpenAI key to run the agent
        openai_key = await get_user_api_key(db, user_id, "openai_api_key")
        search_key = await get_user_api_key(db, user_id, "search_api_key")
        
        if not openai_key:
            send_whatsapp_message(to_whatsapp, "❌ OpenAI API Key is missing. Please configure it in your dashboard settings.")
            return

        send_whatsapp_message(to_whatsapp, "⏳ I'm running your agent now. This may take a moment depending on the complexity of the task...")

        run_record = await execute_agent(
            agent_id=agent_id,
            db=db,
            openai_api_key=openai_key,
            search_api_key=search_key
        )
        
        if run_record.status == "completed" and run_record.output:
            # Send the markdown output back
            send_whatsapp_message(to_whatsapp, f"✅ *Agent Run Complete*\n\n{run_record.output}")
        else:
            err_msg = run_record.error or "Unknown error occurred during execution."
            send_whatsapp_message(to_whatsapp, f"❌ *Agent Run Failed*\n\n{err_msg}")
            
    except Exception as e:
        logger.exception("Error in background agent execution for WhatsApp.")
        send_whatsapp_message(to_whatsapp, f"❌ *System Error*\n\nFailed to execute agent: {str(e)}")

class WebhookPayload(BaseModel):
    body: str
    from_: str

@router.post("/whatsapp/internal-webhook")
async def whatsapp_webhook(payload: dict, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """
    The internal webhook called by the Node.js WhatsApp companion service.
    """
    body = payload.get("body", "").strip()
    from_number = payload.get("from", "")
    
    if not from_number:
        return {"status": "ignored", "reason": "No From number"}
        
    # Standardize the number format to match what we store in the DB
    clean_number = from_number.replace("@c.us", "").replace("whatsapp:", "").replace("+", "").strip()
    
    # 1. Look up the user by whatsapp_number
    # Since users might have saved their number with or without a country code,
    # and WhatsApp sends the country code, a robust way is to check if the
    # saved whatsapp_number ends with the same digits (e.g. last 10 digits for US numbers).
    # Since we can't do an easy ends-with in generic SQL efficiently, we can fetch all users
    # who have a whatsapp_number and do a simple string matching in Python since the user base is small.
    # Or, we can do a LIKE query. Let's do a basic LIKE query matching the last 10 digits of clean_number.
    
    search_suffix = clean_number[-10:] if len(clean_number) >= 10 else clean_number
    
    result = await db.execute(
        select(User).where(User.whatsapp_number.like(f"%{search_suffix}%"))
    )
    user = result.scalars().first() # Get the first match
    
    if not user:
        # If the number isn't linked, tell them to register it
        send_whatsapp_message(
            from_number, 
            "👋 Welcome to SqubeFi! Your WhatsApp number is not recognized. "
            "Please log in to your dashboard, go to the Channels tab, and link this phone number."
        )
        return {"status": "ok", "message": "unregistered user"}

    # 2. Parse the command
    body_lower = body.lower()
    
    # We'll use a simple stateless routing approach.
    # If they type "agents", we list their agents.
    if body_lower in ["agents", "list", "menu", "hi", "hello"]:
        agents_res = await db.execute(
            select(Agent)
            .where(Agent.user_id == user.id)
            .order_by(Agent.name)
        )
        agents = agents_res.scalars().all()
        
        if not agents:
            send_whatsapp_message(from_number, "You don't have any agents created yet. Create one in your dashboard first!")
            return {"status": "ok"}
            
        msg = "🤖 *Your SqubeFi Agents*\n\nReply with the *number* of the agent you want to run:\n\n"
        for idx, agent in enumerate(agents, start=1):
            msg += f"{idx}. {agent.name}\n"
            
        send_whatsapp_message(from_number, msg)
        return {"status": "ok"}
        
    # If they typed a number, check if it corresponds to an agent from their list (sorted alphabetically)
    if body.isdigit():
        idx = int(body)
        agents_res = await db.execute(
            select(Agent)
            .where(Agent.user_id == user.id)
            .order_by(Agent.name)
        )
        agents = agents_res.scalars().all()
        
        if 1 <= idx <= len(agents):
            selected_agent = agents[idx - 1]
            send_whatsapp_message(from_number, f"Executing *{selected_agent.name}*... 🔨")
            
            # Fire off background task so we can return 200 OK to Twilio immediately
            background_tasks.add_task(run_agent_and_reply, selected_agent.id, user.id, db, from_number)
            return {"status": "ok"}
        else:
            send_whatsapp_message(from_number, "❌ Invalid agent number. Reply with 'Agents' to see the list again.")
            return {"status": "ok"}

    # Default fallback
    send_whatsapp_message(
        from_number, 
        "I didn't quite catch that. Try sending *Agents* to see a list of your automated research agents."
    )
    return {"status": "ok"}


# --- New endpoints for the frontend to check QR pairing status ---

@router.get("/whatsapp/status")
async def get_whatsapp_status():
    """Proxy the readiness status from the Node service."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{NODE_SERVICE_URL}/status", timeout=5.0)
            return resp.json()
    except httpx.RequestError:
        # Silently fail if the service is intentionally disabled/down
        return {"ready": False, "error": "WhatsApp service is offline"}

@router.get("/whatsapp/qr")
async def get_whatsapp_qr():
    """Proxy the latest QR code payload from the Node service."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{NODE_SERVICE_URL}/qr", timeout=5.0)
            return resp.json()
    except httpx.RequestError:
        # Silently fail if the service is intentionally disabled/down
        return {"qr": None, "status": "offline", "error": "WhatsApp service is offline"}
