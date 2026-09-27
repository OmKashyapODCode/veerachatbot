"""
FastAPI API routers for all 5 required endpoints + teardown.
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.context_store import get_context_store
from app.core.conversation import get_conversation_manager

router = APIRouter()

# -------------------------------------------------------------------------
# Models
# -------------------------------------------------------------------------

class ContextRequest(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str = ""


class TickRequest(BaseModel):
    now: str
    available_triggers: List[str] = []


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str
    received_at: str = ""
    turn_number: int = 1


# -------------------------------------------------------------------------
# Dependencies (imported lazily to avoid circular imports)
# -------------------------------------------------------------------------

def _get_app_state():
    """Get shared app state from the global state."""
    from app.main import _app_state
    return _app_state


# -------------------------------------------------------------------------
# GET /v1/healthz
# -------------------------------------------------------------------------

@router.get("/healthz")
async def healthz():
    from app.main import _start_time
    store = get_context_store()
    counts = store.counts()
    uptime = int(time.time() - _start_time)
    return {
        "status": "ok",
        "uptime_seconds": uptime,
        "contexts_loaded": counts,
    }


# -------------------------------------------------------------------------
# GET /v1/metadata
# -------------------------------------------------------------------------

@router.get("/metadata")
async def metadata():
    import os
    return {
        "team_name": os.getenv("TEAM_NAME", "Team Vera"),
        "team_members": [m.strip() for m in os.getenv("TEAM_MEMBERS", "Member1").split(",")],
        "model": os.getenv("MODEL_NAME", "gemini-2.0-flash"),
        "approach": (
            "4-context composition engine: LLM (Gemini/OpenAI/Anthropic) with "
            "deterministic fallback. Trigger-kind dispatch, auto-reply detection, "
            "intent-aware reply routing. Temperature=0 for determinism."
        ),
        "contact_email": os.getenv("CONTACT_EMAIL", "contact@example.com"),
        "version": os.getenv("APP_VERSION", "1.0.0"),
        "submitted_at": os.getenv("SUBMITTED_AT", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
    }


# -------------------------------------------------------------------------
# POST /v1/context
# -------------------------------------------------------------------------

@router.post("/context")
async def push_context(body: ContextRequest):
    store = get_context_store()
    result = store.push(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
    )

    if not result.get("accepted") and result.get("reason") == "invalid_scope":
        raise HTTPException(status_code=400, detail=result)

    if not result.get("accepted") and result.get("reason") == "stale_version":
        # Return 409 as specified in the challenge
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content=result)

    return result


# -------------------------------------------------------------------------
# POST /v1/tick
# -------------------------------------------------------------------------

@router.post("/tick")
async def tick(body: TickRequest):
    from app.main import _app_state
    from app.services.tick_service import process_tick

    store = get_context_store()
    conv_manager = get_conversation_manager()
    composer = _app_state.get("composer")
    suppression_set = _app_state.get("suppression_set", set())

    if composer is None:
        return {"actions": []}

    try:
        actions = process_tick(
            available_trigger_ids=body.available_triggers,
            now_str=body.now,
            context_store=store,
            conv_manager=conv_manager,
            composer=composer,
            suppression_set=suppression_set,
        )
        return {"actions": actions}
    except Exception:
        return {"actions": []}


# -------------------------------------------------------------------------
# POST /v1/reply
# -------------------------------------------------------------------------

@router.post("/reply")
async def reply(body: ReplyRequest):
    from app.main import _app_state
    from app.services.reply_service import process_reply

    store = get_context_store()
    conv_manager = get_conversation_manager()
    composer = _app_state.get("composer")

    if composer is None:
        return {
            "action": "end",
            "rationale": "Bot not properly initialized.",
        }

    try:
        result = process_reply(
            conversation_id=body.conversation_id,
            merchant_id=body.merchant_id,
            customer_id=body.customer_id,
            from_role=body.from_role,
            message=body.message,
            turn_number=body.turn_number,
            context_store=store,
            conv_manager=conv_manager,
            composer=composer,
        )
        return result
    except Exception as e:
        return {
            "action": "end",
            "rationale": f"Error processing reply: {str(e)[:100]}",
        }


# -------------------------------------------------------------------------
# POST /v1/teardown (optional)
# -------------------------------------------------------------------------

@router.post("/teardown")
async def teardown():
    """Wipe all state — called by the judge at end of test."""
    store = get_context_store()
    conv_manager = get_conversation_manager()
    from app.main import _app_state

    store.wipe()
    conv_manager.wipe()
    suppression_set = _app_state.get("suppression_set")
    if suppression_set is not None:
        suppression_set.clear()

    return {"status": "wiped", "message": "All state cleared."}
