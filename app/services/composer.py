"""
Main AI composer — combines 4 contexts via LLM with deterministic fallback.
Uses structured JSON output from the LLM.
"""

import json
import re
import os
from typing import Any, Dict, Optional

from app.llm.providers import LLMProvider, extract_json_from_text
from app.services.fallback_composer import compose_fallback


COMPOSER_SYSTEM_PROMPT = """You are Vera, magicpin's merchant AI assistant. You compose WhatsApp messages for Indian merchants.

YOUR TASK: Given 4 context layers (category, merchant, trigger, customer?), produce ONE perfect WhatsApp message.

CRITICAL RULES:
1. NEVER fabricate data not present in the context. No fake prices, fake papers, fake competitors, fake statistics.
2. Use ONLY numbers, names, offers, and facts from the provided context.
3. Match the category voice (dentists=clinical/peer, salons=warm/practical, restaurants=operator, gyms=coach, pharmacies=trustworthy).
4. Use the merchant's language preference (hi-en mix if languages includes "hi").
5. Address merchant/customer by their actual name from context.
6. ONE clear CTA only. Never multiple options.
7. No long preambles ("I hope you're doing well..."). Start with the hook.
8. No re-introduction after first message. No generic "grow your business" lines.
9. No URLs.
10. Be concise — WhatsApp, not email.
11. For customer-facing triggers: set send_as="merchant_on_behalf". For merchant-facing: set send_as="vera".
12. CTA options: "open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "none"

COMPULSION LEVERS (use 1-2 per message):
- Specificity: concrete numbers/dates/sources from context
- Loss aversion: "you're missing X" 
- Social proof: peer benchmarks from category stats
- Effort externalization: "I've drafted X — just say go"
- Curiosity: "want to see who?"
- Single binary commit: Reply YES/STOP

RESPOND WITH VALID JSON ONLY:
{
  "body": "<WhatsApp message text>",
  "cta": "<one of: open_ended|binary_yes_no|binary_confirm_cancel|multi_choice_slot|none>",
  "send_as": "<vera|merchant_on_behalf>",
  "suppression_key": "<from trigger.suppression_key>",
  "rationale": "<1-2 sentences why this message, what it achieves>"
}"""


def _build_compose_prompt(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict],
) -> str:
    """Build the LLM composition prompt with full context."""

    # Category essentials
    cat_slug = category.get("slug", "unknown")
    cat_voice = category.get("voice", {})
    cat_tone = cat_voice.get("tone", "peer_clinical")
    cat_taboos = cat_voice.get("vocab_taboo", [])
    cat_offers = [o.get("title", "") for o in category.get("offer_catalog", [])[:3]]
    peer_stats = category.get("peer_stats", {})

    # Pick most relevant digest item
    digest_items = category.get("digest", [])
    trigger_payload = trigger.get("payload", {})
    top_item_id = trigger_payload.get("top_item_id", trigger_payload.get("alert_id", trigger_payload.get("digest_item_id", "")))
    relevant_digest = []
    if top_item_id:
        item = next((d for d in digest_items if d.get("id") == top_item_id), None)
        if item:
            relevant_digest = [item]
    if not relevant_digest and digest_items:
        relevant_digest = digest_items[:2]

    # Merchant essentials
    m_identity = merchant.get("identity", {})
    m_name = m_identity.get("name", "merchant")
    m_owner = m_identity.get("owner_first_name", "")
    m_languages = m_identity.get("languages", ["en"])
    m_locality = m_identity.get("locality", "")
    m_city = m_identity.get("city", "")
    m_verified = m_identity.get("verified", False)
    m_perf = merchant.get("performance", {})
    m_offers = [o for o in merchant.get("offers", []) if o.get("status") == "active"]
    m_signals = merchant.get("signals", [])
    m_agg = merchant.get("customer_aggregate", {})
    m_conv_hist = merchant.get("conversation_history", [])[-2:] if merchant.get("conversation_history") else []
    m_sub = merchant.get("subscription", {})
    m_review_themes = merchant.get("review_themes", [])[:2]

    # Trigger details
    t_kind = trigger.get("kind", "")
    t_scope = trigger.get("scope", "merchant")
    t_urgency = trigger.get("urgency", 1)
    t_suppression = trigger.get("suppression_key", "")
    t_expires = trigger.get("expires_at", "")

    # Customer details
    cust_section = ""
    if customer:
        c_id = customer.get("identity", {})
        c_rel = customer.get("relationship", {})
        c_state = customer.get("state", "")
        c_prefs = customer.get("preferences", {})
        c_consent = customer.get("consent", {})
        cust_section = f"""
CUSTOMER CONTEXT:
- Name: {c_id.get('name', '')}
- Language preference: {c_id.get('language_pref', 'english')}
- State: {c_state}
- Last visit: {c_rel.get('last_visit', '')}
- Visits total: {c_rel.get('visits_total', 0)}
- Services received: {c_rel.get('services_received', [])[:4]}
- Preferred slot: {c_prefs.get('preferred_slots', '')}
- Consent scope: {c_consent.get('scope', [])}"""

    prompt = f"""COMPOSE A WHATSAPP MESSAGE using these exact context inputs.

=== CATEGORY: {cat_slug} ===
Voice tone: {cat_tone}
Taboo words (NEVER use): {cat_taboos}
Category offer catalog: {cat_offers}
Peer stats: avg_rating={peer_stats.get('avg_rating', 'N/A')}, avg_ctr={peer_stats.get('avg_ctr', 'N/A')}, avg_calls_30d={peer_stats.get('avg_calls_30d', 'N/A')}
Relevant digest items: {json.dumps(relevant_digest, ensure_ascii=False)}

=== MERCHANT CONTEXT ===
Merchant name: {m_name}
Owner first name: {m_owner}
Location: {m_locality}, {m_city}
GBP verified: {m_verified}
Languages: {m_languages}
Subscription: {m_sub.get('status', '')} / {m_sub.get('plan', '')} / {m_sub.get('days_remaining', m_sub.get('days_since_expiry', 'N/A'))} days
Performance (30d): views={m_perf.get('views', 0)}, calls={m_perf.get('calls', 0)}, directions={m_perf.get('directions', 0)}, ctr={m_perf.get('ctr', 'N/A')}
Delta 7d: {m_perf.get('delta_7d', {})}
Active offers: {[o.get('title') for o in m_offers]}
Signals: {m_signals}
Customer aggregate: {m_agg}
Recent conversation history: {m_conv_hist}
Review themes: {m_review_themes}

=== TRIGGER CONTEXT ===
Trigger kind: {t_kind}
Trigger scope: {t_scope}
Urgency (1-5): {t_urgency}
Payload: {json.dumps(trigger_payload, ensure_ascii=False)}
Suppression key (use this exactly): {t_suppression}
Expires at: {t_expires}
{cust_section}

=== YOUR TASK ===
Compose the optimal WhatsApp message for this exact scenario.
- Address merchant/customer by name (use owner_first_name for merchant, customer.identity.name for customer).
- Anchor on 1-2 concrete facts from the context above (numbers, dates, citations).
- Use the suppression_key exactly as provided.
- Determine send_as: "{("merchant_on_behalf" if customer else "vera")}".
- Return ONLY valid JSON matching the schema.

JSON:"""

    return prompt


def _validate_output(result: dict, trigger: dict, category: dict, merchant: dict) -> bool:
    """
    Post-LLM validation.
    Returns True if output is valid and safe to use.
    """
    if not isinstance(result, dict):
        return False

    body = result.get("body", "")
    if not body or len(body) < 10:
        return False

    # Check for required fields
    for field in ("body", "cta", "send_as", "suppression_key", "rationale"):
        if field not in result:
            return False

    # Check CTA is valid
    valid_ctas = {"open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "none"}
    if result.get("cta") not in valid_ctas:
        result["cta"] = "open_ended"

    # Check send_as is valid
    if result.get("send_as") not in ("vera", "merchant_on_behalf"):
        result["send_as"] = "vera"

    # Check for category taboos
    cat_taboos = category.get("voice", {}).get("vocab_taboo", [])
    body_lower = body.lower()
    for taboo in cat_taboos:
        if taboo.lower() in body_lower:
            return False  # Contains prohibited word

    # Check for potential hallucinations (URLs)
    if re.search(r'https?://', body):
        return False

    # Check for fake citations pattern (citation not in digest)
    # Only flag if we see a journal/paper citation not present in the digest
    # (light heuristic — don't be too strict)

    return True


def _repair_output(result: dict, trigger: dict) -> dict:
    """Attempt to repair minor validation failures."""
    if not result:
        return {}

    # Fix CTA
    valid_ctas = {"open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "none"}
    if result.get("cta") not in valid_ctas:
        result["cta"] = "open_ended"

    # Fix send_as
    if result.get("send_as") not in ("vera", "merchant_on_behalf"):
        result["send_as"] = "vera"

    # Fix suppression_key
    if not result.get("suppression_key"):
        result["suppression_key"] = trigger.get("suppression_key", "")

    # Fix rationale
    if not result.get("rationale"):
        result["rationale"] = f"Composed for {trigger.get('kind', 'trigger')}"

    # Remove URLs from body
    body = result.get("body", "")
    body = re.sub(r'https?://\S+', '', body).strip()
    result["body"] = body

    return result


class ComposerService:
    """
    Main composition service.
    Tries LLM first; falls back to deterministic templates if LLM fails.
    """

    def __init__(self, llm: Optional[LLMProvider] = None):
        self.llm = llm

    def compose(
        self,
        category: dict,
        merchant: dict,
        trigger: dict,
        customer: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """
        Compose a message from the 4 contexts.
        Returns a dict with: body, cta, send_as, suppression_key, rationale
        """
        # Try LLM if available
        if self.llm is not None:
            try:
                result = self._compose_with_llm(category, merchant, trigger, customer)
                if result and _validate_output(result, trigger, category, merchant):
                    return result

                # Try repair
                if result:
                    repaired = _repair_output(result, trigger)
                    if repaired and repaired.get("body"):
                        return repaired

            except Exception as e:
                # LLM failed — use fallback
                pass

        # Deterministic fallback
        return compose_fallback(category, merchant, trigger, customer)

    def _compose_with_llm(
        self,
        category: dict,
        merchant: dict,
        trigger: dict,
        customer: Optional[dict],
    ) -> Optional[dict]:
        """Call the LLM and parse the response."""
        prompt = _build_compose_prompt(category, merchant, trigger, customer)

        try:
            raw = self.llm.complete(prompt, COMPOSER_SYSTEM_PROMPT)
            result = extract_json_from_text(raw)
            return result
        except Exception:
            return None

    def compose_reply(
        self,
        conversation_state,
        merchant_message: str,
        intent: str,
        category: Optional[dict],
        merchant: dict,
        trigger: Optional[dict],
        customer: Optional[dict],
    ) -> Dict[str, Any]:
        """
        Compose a reply to a merchant/customer message.
        Returns: {action: "send"|"wait"|"end", body?, cta?, rationale}
        """
        # Handle hostile intent
        if intent == "hostile":
            return {
                "action": "end",
                "rationale": "Merchant expressed frustration/hostility. Closing gracefully to respect their preference.",
            }

        # Handle auto-reply
        if intent == "auto_reply":
            auto_count = conversation_state.auto_reply_count
            if auto_count == 1:
                # First auto-reply — try to reach the owner
                owner = merchant.get("identity", {}).get("owner_first_name", "")
                name = merchant.get("identity", {}).get("name", "merchant")
                body = (
                    f"Looks like an automated reply 😊 "
                    f"When {owner or 'the owner'} at {name} gets this — just reply YES to continue, "
                    f"or tell me a good time to follow up."
                )
                return {
                    "action": "send",
                    "body": body,
                    "cta": "binary_yes_no",
                    "rationale": "Detected auto-reply; sending one bridging message to reach the owner.",
                }
            elif auto_count == 2:
                return {
                    "action": "wait",
                    "wait_seconds": 86400,
                    "rationale": "Same auto-reply again — owner not at phone. Backing off 24h.",
                }
            else:
                return {
                    "action": "end",
                    "rationale": f"Auto-reply detected {auto_count} times in a row. No engagement signal; closing conversation.",
                }

        # Handle explicit no/opt-out
        if intent == "no":
            return {
                "action": "end",
                "rationale": "Merchant indicated not interested. Closing conversation; will not re-engage on this topic.",
            }

        # Handle wait
        if intent == "wait":
            return {
                "action": "wait",
                "wait_seconds": 3600,
                "rationale": "Merchant asked for time. Backing off 1 hour.",
            }

        # Handle commit / yes — take action immediately
        if intent in ("commit", "yes"):
            return self._compose_action_reply(
                conversation_state, merchant_message, category, merchant, trigger, customer
            )

        # Handle question / unknown — try LLM reply
        return self._compose_conversation_reply(
            conversation_state, merchant_message, intent, category, merchant, trigger, customer
        )

    def _compose_action_reply(
        self,
        conv_state,
        merchant_message: str,
        category: Optional[dict],
        merchant: dict,
        trigger: Optional[dict],
        customer: Optional[dict],
    ) -> Dict[str, Any]:
        """Merchant committed — switch to action mode immediately."""
        if self.llm and category and trigger:
            try:
                prompt = self._build_action_reply_prompt(
                    conv_state, merchant_message, category, merchant, trigger, customer
                )
                raw = self.llm.complete(prompt, COMPOSER_SYSTEM_PROMPT)
                result = extract_json_from_text(raw)
                if result and result.get("body"):
                    # Wrap in reply format
                    return {
                        "action": "send",
                        "body": result.get("body", ""),
                        "cta": result.get("cta", "open_ended"),
                        "rationale": result.get("rationale", "Merchant committed — taking action."),
                    }
            except Exception:
                pass

        # Fallback action reply
        owner = merchant.get("identity", {}).get("owner_first_name", "") or merchant.get("identity", {}).get("name", "")
        trigger_kind = trigger.get("kind", "") if trigger else ""

        if "planning" in trigger_kind or "intent" in trigger_kind:
            body = (
                f"Done — I'll draft the plan now and have a starter version ready in 2 minutes. "
                f"I'll send it over for your review."
            )
        elif "recall" in trigger_kind or "customer" in trigger_kind.lower():
            body = "Sending the message to the customer now. I'll let you know once it's delivered."
        elif "research" in trigger_kind or "digest" in trigger_kind:
            body = "Pulling the abstract now and drafting a patient-ed note you can share. Give me 60 seconds."
        elif "post" in trigger_kind or "profile" in trigger_kind:
            body = "Perfect — drafting the post now. I'll send you a preview for approval in 2 minutes."
        else:
            body = (
                f"Great, proceeding now. I'll have everything ready for your review shortly. "
                f"Reply STOP anytime if you change your mind."
            )

        return {
            "action": "send",
            "body": body,
            "cta": "none",
            "rationale": "Merchant committed to action — proceeding immediately without further qualification.",
        }

    def _compose_conversation_reply(
        self,
        conv_state,
        merchant_message: str,
        intent: str,
        category: Optional[dict],
        merchant: dict,
        trigger: Optional[dict],
        customer: Optional[dict],
    ) -> Dict[str, Any]:
        """Handle general conversation replies (questions, unknown intent)."""
        if self.llm and category:
            try:
                prompt = self._build_conversation_reply_prompt(
                    conv_state, merchant_message, intent, category, merchant, trigger, customer
                )
                raw = self.llm.complete(prompt, COMPOSER_SYSTEM_PROMPT)
                result = extract_json_from_text(raw)
                if result and result.get("body"):
                    action = result.get("action", "send")
                    if action not in ("send", "wait", "end"):
                        action = "send"
                    resp = {"action": action, "rationale": result.get("rationale", "")}
                    if action == "send":
                        resp["body"] = result.get("body", "")
                        resp["cta"] = result.get("cta", "open_ended")
                    elif action == "wait":
                        resp["wait_seconds"] = result.get("wait_seconds", 3600)
                    return resp
            except Exception:
                pass

        # Fallback conversational reply
        owner = merchant.get("identity", {}).get("owner_first_name", "") or ""
        trigger_kind = trigger.get("kind", "") if trigger else ""

        if intent == "question":
            body = (
                f"Good question! Based on what I can see for your account — "
                f"the most relevant next step would be around your {trigger_kind.replace('_', ' ') or 'current trigger'}. "
                f"Want me to share the details?"
            )
        else:
            body = (
                f"Got it. Is there anything specific you'd like help with — "
                f"profile updates, customer outreach, or offers?"
            )

        return {
            "action": "send",
            "body": body,
            "cta": "open_ended",
            "rationale": f"Conversational reply to merchant message (intent: {intent})",
        }

    def _build_action_reply_prompt(
        self, conv_state, merchant_message, category, merchant, trigger, customer
    ) -> str:
        owner = merchant.get("identity", {}).get("owner_first_name", "") or merchant.get("identity", {}).get("name", "")
        t_kind = trigger.get("kind", "") if trigger else ""
        bot_history = conv_state.bot_messages[-2:] if conv_state.bot_messages else []
        merchant_history = conv_state.merchant_messages[-2:] if conv_state.merchant_messages else []
        active_offers = [o.get("title", "") for o in merchant.get("offers", []) if o.get("status") == "active"]

        return f"""The merchant just committed to taking action: "{merchant_message}"

CONTEXT:
- Merchant: {owner} at {merchant.get('identity', {}).get('name', '')}
- Trigger kind: {t_kind}
- Active offers: {active_offers}
- Category: {category.get('slug', '')}
- Recent bot messages: {bot_history}
- Recent merchant messages: {merchant_history}

TASK: Compose the bot's IMMEDIATE ACTION response.
- Switch from qualifying to action immediately.
- DO NOT ask another qualifying question.
- Tell them what you're doing/drafting/sending.
- Be concrete about timing ("2 minutes", "ready now").
- Include a confirmation CTA if appropriate.

Return JSON: {{"body": "...", "cta": "...", "rationale": "..."}}"""

    def _build_conversation_reply_prompt(
        self, conv_state, merchant_message, intent, category, merchant, trigger, customer
    ) -> str:
        owner = merchant.get("identity", {}).get("owner_first_name", "") or merchant.get("identity", {}).get("name", "")
        t_kind = trigger.get("kind", "") if trigger else ""
        bot_history = conv_state.bot_messages[-3:] if conv_state.bot_messages else []

        return f"""Continue this merchant conversation.

MERCHANT SAID: "{merchant_message}"
DETECTED INTENT: {intent}

CONTEXT:
- Merchant: {owner} at {merchant.get('identity', {}).get('name', '')}
- Trigger: {t_kind}
- Category: {category.get('slug', '')}
- Previous bot messages: {bot_history}
- Turn number: {conv_state.turn_number}

TASK: Compose the best next response. The merchant's intent is "{intent}".
- If it's a question, answer from context (no fabrication).
- If it's off-topic (e.g., "help me with GST"), politely redirect to Vera's scope.
- If unclear, ask one clarifying question.
- Stay on mission.

Return JSON: {{"action": "send|wait|end", "body": "...", "cta": "...", "wait_seconds": 0, "rationale": "..."}}
Only include "body" and "cta" when action is "send".
Only include "wait_seconds" when action is "wait"."""
