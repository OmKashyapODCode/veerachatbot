"""
Tick service — handles POST /v1/tick logic.
Decides which triggers to act on, generates proactive messages.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from app.core.context_store import ContextStore
from app.core.conversation import ConversationManager
from app.services.composer import ComposerService


def _is_trigger_expired(trigger: dict) -> bool:
    """Check if a trigger has expired."""
    expires_at = trigger.get("expires_at", "")
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > exp
    except Exception:
        return False


def _priority_score(trigger: dict) -> int:
    """Higher = more urgent. Used to sort triggers."""
    urgency = trigger.get("urgency", 1)
    kind = trigger.get("kind", "")
    # Boost certain kinds
    boosts = {
        "supply_alert": 10,
        "regulation_change": 8,
        "renewal_due": 6,
        "active_planning_intent": 7,
        "recall_due": 5,
        "perf_dip": 4,
        "chronic_refill_due": 5,
    }
    return urgency + boosts.get(kind, 0)


def process_tick(
    available_trigger_ids: List[str],
    now_str: str,
    context_store: ContextStore,
    conv_manager: ConversationManager,
    composer: ComposerService,
    suppression_set: Set[str],
) -> List[Dict[str, Any]]:
    """
    Process a tick request.
    Returns a list of action dicts for the judge.
    """
    actions = []
    seen_merchants_this_tick: Set[str] = set()  # One action per merchant per tick
    seen_conv_ids: Set[str] = set()

    # Collect and sort triggers by priority
    # Note: The judge controls which triggers are in available_triggers.
    # We DO NOT skip expired ones — the judge already filters those.
    # We only skip ones we've already acted on (suppression_key tracking).
    candidate_triggers = []
    for trg_id in available_trigger_ids:
        trg = context_store.get("trigger", trg_id)
        if not trg:
            continue
        suppression_key = trg.get("suppression_key", "")
        if suppression_key and suppression_key in suppression_set:
            continue
        candidate_triggers.append((trg_id, trg))

    # Sort by priority descending
    candidate_triggers.sort(key=lambda x: _priority_score(x[1]), reverse=True)

    for trg_id, trg in candidate_triggers:
        if len(actions) >= 20:  # Per-tick cap
            break

        merchant_id = trg.get("merchant_id", "")
        customer_id = trg.get("customer_id")

        if not merchant_id:
            continue

        # Only one action per merchant per tick
        if merchant_id in seen_merchants_this_tick:
            continue

        # Get merchant context
        merchant = context_store.get("merchant", merchant_id)
        if not merchant:
            continue

        # Get category context
        category_slug = merchant.get("category_slug", "")
        category = context_store.get("category", category_slug)
        if not category:
            continue

        # Get customer context if needed
        customer = None
        if customer_id:
            customer = context_store.get("customer", customer_id)

        # Generate unique conversation ID (never reuse existing)
        safe_mid = merchant_id.replace("_", "")[:20]
        safe_tid = trg_id.replace("_", "")[:15]
        conv_id = f"conv_{safe_mid}_{safe_tid}_{uuid.uuid4().hex[:6]}"
        while conv_manager.is_used(conv_id) or conv_id in seen_conv_ids:
            conv_id = f"conv_{safe_mid}_{safe_tid}_{uuid.uuid4().hex[:6]}"
        seen_conv_ids.add(conv_id)

        # Compose the message
        try:
            composed = composer.compose(category, merchant, trg, customer)
        except Exception:
            continue

        body = composed.get("body", "")
        if not body:
            continue

        suppression_key = composed.get("suppression_key", trg.get("suppression_key", ""))

        # Build template params (first 3 meaningful fragments)
        m_name = merchant.get("identity", {}).get("name", merchant_id)
        m_owner = merchant.get("identity", {}).get("owner_first_name", m_name)
        template_params = [
            m_owner or m_name,
            body[:100] if len(body) > 100 else body,
            composed.get("cta", "open_ended"),
        ]

        # Determine template name by trigger kind
        kind = trg.get("kind", "generic")
        template_name_map = {
            "research_digest": "vera_research_digest_v1",
            "regulation_change": "vera_compliance_alert_v1",
            "recall_due": "merchant_recall_reminder_v1",
            "perf_dip": "vera_perf_alert_v1",
            "perf_spike": "vera_perf_spike_v1",
            "renewal_due": "vera_renewal_v1",
            "festival_upcoming": "vera_festival_v1",
            "ipl_match_today": "vera_ipl_v1",
            "supply_alert": "vera_supply_alert_v1",
            "chronic_refill_due": "merchant_refill_reminder_v1",
        }
        template_name = template_name_map.get(kind, f"vera_{kind}_v1")

        action = {
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": composed.get("send_as", "vera"),
            "trigger_id": trg_id,
            "template_name": template_name,
            "template_params": template_params,
            "body": body,
            "cta": composed.get("cta", "open_ended"),
            "suppression_key": suppression_key,
            "rationale": composed.get("rationale", ""),
        }

        actions.append(action)
        seen_merchants_this_tick.add(merchant_id)

        # Track conversation state
        conv_state = conv_manager.get_or_create(
            conversation_id=conv_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=trg_id,
        )
        conv_state.record_bot_message(body)
        conv_state.metadata["category_slug"] = category_slug
        conv_state.metadata["trigger_kind"] = kind

        # Add to suppression set
        if suppression_key:
            suppression_set.add(suppression_key)

    return actions
