"""Agent routes: create, manage, and execute AI agents."""

import datetime

from croniter import croniter
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user, get_premium_user, get_user_api_key
from ..database import async_session, get_db
from ..models import Agent, AgentRun, User, AllowedUser
from ..services.agent_service import execute_agent

router = APIRouter(prefix="/api/agents", tags=["agents"])


# --------------------------------------------------------------------------
# Pydantic schemas
# --------------------------------------------------------------------------

class AgentCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    instruction: str = Field(..., min_length=1)
    is_shared: bool = False
    schedule_type: str = Field(default="manual")  # manual, one_time, recurring
    schedule_cron: str | None = None
    scheduled_at: datetime.datetime | None = None
    send_email_on_run: bool = False
    email_report_to: str | None = None
    tracked_company_id: int | None = None   # link back to the source tracked company


class AgentUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    instruction: str | None = None
    is_shared: bool | None = None
    schedule_type: str | None = None
    schedule_cron: str | None = None
    scheduled_at: datetime.datetime | None = None
    send_email_on_run: bool | None = None
    email_report_to: str | None = None
    
class AgentStatusIn(BaseModel):
    status: str = Field(...)  # active, paused, stopped


class AgentRunOut(BaseModel):
    id: int
    agent_id: int
    status: str
    started_at: datetime.datetime
    completed_at: datetime.datetime | None = None
    output: str | None = None
    error: str | None = None
    created_at: datetime.datetime


class AgentOut(BaseModel):
    id: int
    user_id: int
    name: str
    description: str | None = None
    instruction: str
    is_shared: bool
    cloned_from_id: int | None = None
    status: str
    schedule_type: str
    schedule_cron: str | None = None
    scheduled_at: datetime.datetime | None = None
    send_email_on_run: bool
    email_report_to: str | None = None
    last_run_at: datetime.datetime | None = None
    next_run_at: datetime.datetime | None = None
    tracked_company_id: int | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    latest_run: AgentRunOut | None = None
    creator_name: str | None = None
    creator_picture: str | None = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _compute_next_run(agent: Agent) -> datetime.datetime | None:
    """Compute the next run time based on schedule type."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if agent.schedule_type == "recurring" and agent.schedule_cron:
        try:
            cron = croniter(agent.schedule_cron, now)
            return cron.get_next(datetime.datetime)
        except (ValueError, KeyError):
            return None
    elif agent.schedule_type == "one_time" and agent.scheduled_at:
        return agent.scheduled_at if agent.scheduled_at > now else None
    return None


def _agent_to_out(agent: Agent, include_creator: bool = False) -> AgentOut:
    """Convert Agent model to AgentOut schema."""
    latest_run = None
    if agent.runs:
        r = agent.runs[0]  # Already ordered desc by created_at
        latest_run = AgentRunOut(
            id=r.id, agent_id=r.agent_id, status=r.status,
            started_at=r.started_at, completed_at=r.completed_at,
            output=r.output, error=r.error, created_at=r.created_at,
        )

    return AgentOut(
        id=agent.id, user_id=agent.user_id, name=agent.name,
        description=agent.description, instruction=agent.instruction,
        is_shared=agent.is_shared, cloned_from_id=agent.cloned_from_id,
        status=agent.status, schedule_type=agent.schedule_type,
        schedule_cron=agent.schedule_cron, scheduled_at=agent.scheduled_at,
        send_email_on_run=agent.send_email_on_run, email_report_to=agent.email_report_to,
        last_run_at=agent.last_run_at, next_run_at=agent.next_run_at,
        created_at=agent.created_at, updated_at=agent.updated_at,
        latest_run=latest_run,
        creator_name=agent.user.name if include_creator else None,
        creator_picture=agent.user.picture if include_creator else None,
    )


# --------------------------------------------------------------------------
# Background task helper
# --------------------------------------------------------------------------

async def _run_agent_background(agent_id: int, user_id: int, run_id: int):
    """Run agent in background with its own DB session."""
    from ..auth import decrypt_value as _decrypt

    async with async_session() as db:
        openai_key = await get_user_api_key(db, user_id, "openai_api_key")
        if not openai_key:
            # Mark existing run as failed
            result = await db.execute(
                select(AgentRun).where(AgentRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if run:
                run.status = "failed"
                run.error = "OpenAI API key not configured. Please add it in Settings."
                run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                await db.commit()
            return

        # Fetch user's preferred model
        model_pref = await get_user_api_key(db, user_id, "openai_model")
        model = model_pref or "gpt-4o-mini"

        search_key = await get_user_api_key(db, user_id, "search_api_key")
        await execute_agent(agent_id, db, openai_key, search_key, run_id=run_id, model=model)


# --------------------------------------------------------------------------
# POST /api/agents — create agent
# --------------------------------------------------------------------------

@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(
    body: AgentCreateIn,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
):
    # Validate cron expression
    if body.schedule_type == "recurring":
        if not body.schedule_cron:
            raise HTTPException(400, "schedule_cron is required for recurring agents")
        if not croniter.is_valid(body.schedule_cron):
            raise HTTPException(400, f"Invalid cron expression: {body.schedule_cron}")
    elif body.schedule_type == "one_time":
        if not body.scheduled_at:
            raise HTTPException(400, "scheduled_at is required for one-time agents")
    elif body.schedule_type != "manual":
        raise HTTPException(400, f"Invalid schedule_type: {body.schedule_type}")

    if body.send_email_on_run and not body.email_report_to:
        raise HTTPException(400, "email_report_to is required when send_email_on_run is enabled")

    agent = Agent(
        user_id=user.id,
        name=body.name,
        description=body.description,
        instruction=body.instruction,
        is_shared=body.is_shared,
        status="active",
        schedule_type=body.schedule_type,
        schedule_cron=body.schedule_cron,
        scheduled_at=body.scheduled_at,
        send_email_on_run=body.send_email_on_run,
        email_report_to=body.email_report_to,
        tracked_company_id=body.tracked_company_id,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    # Compute next_run_at
    agent.next_run_at = _compute_next_run(agent)
    await db.commit()

    # Re-fetch with relationships
    result = await db.execute(
        select(Agent).options(selectinload(Agent.runs), selectinload(Agent.user))
        .where(Agent.id == agent.id)
    )
    agent = result.scalar_one()
    return _agent_to_out(agent)


# --------------------------------------------------------------------------
# GET /api/agents/community — list shared agents
# --------------------------------------------------------------------------

@router.get("/community", response_model=list[AgentOut])
async def list_community_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Agent)
        .options(selectinload(Agent.runs), selectinload(Agent.user))
        .where(Agent.is_shared == True)
        .order_by(Agent.created_at.desc())
    )
    agents = result.scalars().all()
    return [_agent_to_out(a, include_creator=True) for a in agents]


# --------------------------------------------------------------------------
# GET /api/agents — list user's agents
# --------------------------------------------------------------------------

@router.get("", response_model=list[AgentOut])
async def list_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Agent)
        .options(selectinload(Agent.runs), selectinload(Agent.user))
        .where(Agent.user_id == user.id)
        .order_by(Agent.created_at.desc())
    )
    agents = result.scalars().all()
    return [_agent_to_out(a) for a in agents]


# --------------------------------------------------------------------------
# GET /api/agents/{id} — get single agent
# --------------------------------------------------------------------------

@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Agent)
        .options(selectinload(Agent.runs), selectinload(Agent.user))
        .where(Agent.id == agent_id, Agent.user_id == user.id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return _agent_to_out(agent)


# --------------------------------------------------------------------------
# PATCH /api/agents/{id} — update agent
# --------------------------------------------------------------------------

@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: int,
    body: AgentUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Agent)
        .options(selectinload(Agent.runs), selectinload(Agent.user))
        .where(Agent.id == agent_id, Agent.user_id == user.id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Agent not found")

    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.instruction is not None:
        agent.instruction = body.instruction
    if body.is_shared is not None:
        agent.is_shared = body.is_shared
    if body.schedule_type is not None:
        if body.schedule_type == "recurring" and not (body.schedule_cron or agent.schedule_cron):
            raise HTTPException(400, "schedule_cron required for recurring")
        agent.schedule_type = body.schedule_type
    if body.schedule_cron is not None:
        if not croniter.is_valid(body.schedule_cron):
            raise HTTPException(400, f"Invalid cron: {body.schedule_cron}")
        agent.schedule_cron = body.schedule_cron
    if body.scheduled_at is not None:
        agent.scheduled_at = body.scheduled_at
    if body.send_email_on_run is not None:
        agent.send_email_on_run = body.send_email_on_run
    if body.email_report_to is not None:
        agent.email_report_to = body.email_report_to

    if agent.send_email_on_run and not agent.email_report_to:
        raise HTTPException(400, "email_report_to is required when send_email_on_run is enabled")

    agent.next_run_at = _compute_next_run(agent)
    await db.commit()
    await db.refresh(agent)

    return _agent_to_out(agent)


# --------------------------------------------------------------------------
# PATCH /api/agents/{id}/status — update status
# --------------------------------------------------------------------------

@router.patch("/{agent_id}/status", response_model=AgentOut)
async def update_agent_status(
    agent_id: int,
    body: AgentStatusIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.status not in ("active", "paused", "stopped"):
        raise HTTPException(400, f"Invalid status: {body.status}")

    result = await db.execute(
        select(Agent)
        .options(selectinload(Agent.runs), selectinload(Agent.user))
        .where(Agent.id == agent_id, Agent.user_id == user.id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Agent not found")

    agent.status = body.status
    if body.status == "active":
        agent.next_run_at = _compute_next_run(agent)
    else:
        agent.next_run_at = None
    await db.commit()
    await db.refresh(agent)

    return _agent_to_out(agent)


# --------------------------------------------------------------------------
# DELETE /api/agents/{id} — delete agent
# --------------------------------------------------------------------------

@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user.id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Agent not found")

    await db.delete(agent)
    await db.commit()
    return {"ok": True, "deleted": agent_id}


# --------------------------------------------------------------------------
# POST /api/agents/{id}/run — trigger manual run
# --------------------------------------------------------------------------

@router.post("/{agent_id}/run", response_model=AgentRunOut)
async def trigger_run(
    agent_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.user_id == user.id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Agent not found")

    # Check for already running
    running = await db.execute(
        select(AgentRun).where(
            AgentRun.agent_id == agent_id,
            AgentRun.status == "running",
        )
    )
    if running.scalar_one_or_none():
        raise HTTPException(409, "Agent is already running")

    # Create placeholder run
    now = datetime.datetime.now(datetime.timezone.utc)
    run = AgentRun(agent_id=agent_id, status="running", started_at=now)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    # Launch background execution
    background_tasks.add_task(_run_agent_background, agent_id, user.id, run.id)

    return AgentRunOut(
        id=run.id, agent_id=run.agent_id, status=run.status,
        started_at=run.started_at, completed_at=run.completed_at,
        output=run.output, error=run.error, created_at=run.created_at,
    )


# --------------------------------------------------------------------------
# GET /api/agents/{id}/runs — run history
# --------------------------------------------------------------------------

@router.get("/{agent_id}/runs", response_model=list[AgentRunOut])
async def list_runs(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify ownership
    agent_check = await db.execute(
        select(Agent.id).where(Agent.id == agent_id, Agent.user_id == user.id)
    )
    if not agent_check.scalar_one_or_none():
        raise HTTPException(404, "Agent not found")

    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.agent_id == agent_id)
        .order_by(AgentRun.created_at.desc())
        .limit(20)
    )
    runs = result.scalars().all()
    return [
        AgentRunOut(
            id=r.id, agent_id=r.agent_id, status=r.status,
            started_at=r.started_at, completed_at=r.completed_at,
            output=r.output, error=r.error, created_at=r.created_at,
        )
        for r in runs
    ]


# --------------------------------------------------------------------------
# GET /api/agents/{id}/runs/{run_id} — get specific run
# --------------------------------------------------------------------------

@router.get("/{agent_id}/runs/{run_id}", response_model=AgentRunOut)
async def get_run(
    agent_id: int,
    run_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify ownership
    agent_check = await db.execute(
        select(Agent.id).where(Agent.id == agent_id, Agent.user_id == user.id)
    )
    if not agent_check.scalar_one_or_none():
        raise HTTPException(404, "Agent not found")

    result = await db.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.agent_id == agent_id)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found")

    return AgentRunOut(
        id=run.id, agent_id=run.agent_id, status=run.status,
        started_at=run.started_at, completed_at=run.completed_at,
        output=run.output, error=run.error, created_at=run.created_at,
    )


# --------------------------------------------------------------------------
# POST /api/agents/{id}/clone — clone a community agent
# --------------------------------------------------------------------------

@router.post("/{agent_id}/clone", response_model=AgentOut, status_code=201)
async def clone_agent(
    agent_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Find the source agent (must be shared)
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.is_shared == True)
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(404, "Community agent not found")

    # Create copy for user
    clone = Agent(
        user_id=user.id,
        name=source.name,
        description=source.description,
        instruction=source.instruction,
        is_shared=False,
        cloned_from_id=source.id,
        status="active",
        schedule_type="manual",
    )
    db.add(clone)
    await db.commit()
    await db.refresh(clone)

    # Re-fetch with relationships
    result = await db.execute(
        select(Agent).options(selectinload(Agent.runs), selectinload(Agent.user))
        .where(Agent.id == clone.id)
    )
    clone = result.scalar_one()
    return _agent_to_out(clone)
