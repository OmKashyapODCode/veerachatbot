"""
Reply service — handles POST /v1/reply logic.
Maintains conversation state, detects intent, routes to action.
"""

from typing import Any, Dict, Optional

from app.core.context_store import ContextStore
from app.core.conversation import ConversationManager, ConversationState
from app.services.composer import ComposerService


def process_reply(
    conversation_id: str,
    merchant_id: Optional[str],
    customer_id: Optional[str],
    from_role: str,
    message: str,
    turn_number: int,
    context_store: ContextStore,
    conv_manager: ConversationManager,
    composer: ComposerService,
) -> Dict[str, Any]:
    """
    Handle a reply from the judge (merchant or customer).
    Returns one of: {action: send|wait|end, ...}
    """
    # Get or create conversation state
    conv_state = conv_manager.get_or_create(
        conversation_id=conversation_id,
        merchant_id=merchant_id or "",
        customer_id=customer_id,
    )
    conv_state.turn_number = turn_number

    # Gather contexts early to get actual_merchant_id
    actual_merchant_id = conv_state.merchant_id or merchant_id or ""
    actual_customer_id = conv_state.customer_id or customer_id

    # Record the incoming message and detect intent
    intent = conv_state.record_merchant_message(message)

    # Manage global auto-reply counter
    if intent == "auto_reply":
        # Increment global counter and update local state for the composer
        conv_state.auto_reply_count = conv_manager.increment_merchant_auto_reply(actual_merchant_id)
    else:
        # Reset global counter on real reply
        conv_manager.reset_merchant_auto_reply(actual_merchant_id)
        conv_state.auto_reply_count = 0

    # If conversation was explicitly closed, just end
    if not conv_state.is_active:
        return {
            "action": "end",
            "rationale": "Conversation was already closed.",
        }

    # Gather contexts
    actual_merchant_id = conv_state.merchant_id or merchant_id or ""
    actual_customer_id = conv_state.customer_id or customer_id

    merchant = context_store.get("merchant", actual_merchant_id) if actual_merchant_id else {}
    category_slug = (
        (merchant.get("category_slug", "") if merchant else "") or
        conv_state.metadata.get("category_slug", "")
    )
    category = context_store.get("category", category_slug) if category_slug else None

    trigger_id = conv_state.trigger_id
    trigger = context_store.get("trigger", trigger_id) if trigger_id else None

    customer = context_store.get("customer", actual_customer_id) if actual_customer_id else None

    # If merchant/customer not in context, still respond politely
    if not merchant:
        merchant = {"identity": {"name": "merchant"}}

    # Let composer decide the reply
    result = composer.compose_reply(
        conversation_state=conv_state,
        merchant_message=message,
        intent=intent,
        category=category,
        merchant=merchant,
        trigger=trigger,
        customer=customer,
    )

    # Record bot message if sending
    if result.get("action") == "send":
        body = result.get("body", "")
        # Anti-repetition: don't send the same body twice in this conversation
        if body and conv_state.is_repeated_bot_message(body):
            # Modify slightly to avoid exact repeat penalty
            body = body.rstrip() + " Let me know if you'd like more details."
            result["body"] = body
        if body:
            conv_state.record_bot_message(body)

    # Close conversation if ending
    if result.get("action") == "end":
        conv_state.close()

    return result
