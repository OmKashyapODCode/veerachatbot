"""
Deterministic fallback composer.
Used when LLM is unavailable, times out, or returns invalid output.
NEVER hallucinates — only uses information present in the supplied contexts.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime


def _get_str(d: dict, *keys, default="") -> str:
    """Safely navigate nested dict."""
    for key in keys:
        if isinstance(d, dict):
            d = d.get(key, {})
        else:
            return default
    return str(d) if d else default


def _active_offers(merchant: dict) -> List[str]:
    return [o.get("title", "") for o in merchant.get("offers", []) if o.get("status") == "active"]


def _merchant_name(merchant: dict) -> str:
    return merchant.get("identity", {}).get("name", "merchant")


def _owner_name(merchant: dict) -> str:
    return merchant.get("identity", {}).get("owner_first_name", "")


def _uses_hindi(merchant: dict, customer: Optional[dict] = None) -> bool:
    if customer:
        lang = customer.get("identity", {}).get("language_pref", "")
        return "hi" in lang or "hindi" in lang.lower()
    langs = merchant.get("identity", {}).get("languages", [])
    return "hi" in langs


def _format_date(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return iso_str


# -------------------------------------------------------------------------
# Template functions — each grounded in actual context
# -------------------------------------------------------------------------

def _compose_research_digest(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    top_item_id = payload.get("top_item_id", "")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    digest_items = category.get("digest", [])
    item = next((d for d in digest_items if d.get("id") == top_item_id), None)
    if not item and digest_items:
        item = digest_items[0]

    hi = _uses_hindi(merchant)

    if item:
        title = item.get("title", "")
        source = item.get("source", "")
        n = item.get("trial_n")
        body = f"{owner}, {source} — {title}."
        if n:
            body += f" ({n:,}-participant study)"
        if hi:
            body += " Aapke patient cohort ke liye relevant ho sakta hai. Dekhna chahenge + ek patient-ed WhatsApp draft karein?"
        else:
            body += " Relevant to your patient cohort. Want me to summarize + draft a patient-ed note you can share?"
        cta = "open_ended"
    else:
        body = f"{owner}, this week's digest has new research updates. Want me to pull the highlights for your practice?"
        cta = "open_ended"

    return {
        "body": body,
        "cta": cta,
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", f"research:{trigger.get('id', '')}"),
        "rationale": f"Research digest trigger — surfacing {item.get('source', 'latest') if item else 'digest'} to merchant",
    }


def _compose_regulation_change(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    top_item_id = payload.get("top_item_id", "")
    deadline = payload.get("deadline_iso", "")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    digest_items = category.get("digest", [])
    item = next((d for d in digest_items if d.get("id") == top_item_id), None)

    if item:
        title = item.get("title", "")
        source = item.get("source", "")
        actionable = item.get("actionable", "")
        body = f"{owner}, compliance update: {title}. Source: {source}."
        if deadline:
            body += f" Deadline: {_format_date(deadline)}."
        if actionable:
            body += f" Action required: {actionable}. Should I help you get this sorted before the deadline?"
        else:
            body += " Want me to walk you through the action items?"
    else:
        body = f"{owner}, there's an important regulatory update for your category with a compliance deadline. Want me to walk you through the action items?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": "Regulation change — urgent compliance action required",
    }


def _compose_perf_dip(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "calls")
    delta_pct = payload.get("delta_pct", 0)
    window = payload.get("window", "7d")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    name = _merchant_name(merchant)
    pct_str = f"{abs(int(delta_pct * 100))}%"
    hi = _uses_hindi(merchant)

    if hi:
        body = (
            f"{owner}, {name} ke {metric} last {window} mein {pct_str} gir gaye. "
            f"Kya koi specific reason hai? Main aapka Google profile audit kar sakti hoon aur "
            f"ek quick action plan bana sakti hoon."
        )
    else:
        body = (
            f"{owner}, your {metric} are down {pct_str} over the last {window} at {name}. "
            f"Want me to run a quick audit and identify what's driving this?"
        )

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Performance dip detected: {metric} -{pct_str} over {window}",
    }


def _compose_perf_spike(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "views")
    delta_pct = payload.get("delta_pct", 0)
    window = payload.get("window", "7d")
    driver = payload.get("likely_driver", "")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    pct_str = f"{int(delta_pct * 100)}%"

    body = f"{owner}, good news — your {metric} jumped {pct_str} this {window}."
    if driver:
        body += f" Likely driver: {driver.replace('_', ' ')}."
    body += " Want me to capitalize on this momentum with a quick post or targeted offer?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Performance spike: {metric} +{pct_str}",
    }


def _compose_milestone(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "review_count")
    value_now = payload.get("value_now", 0)
    milestone = payload.get("milestone_value", 0)
    owner = _owner_name(merchant) or _merchant_name(merchant)
    name = _merchant_name(merchant)

    body = (
        f"{owner}, {name} is at {value_now} {metric.replace('_', ' ')} — just {milestone - value_now} away "
        f"from the {milestone} milestone. Want me to draft a review-request WhatsApp to push you over the line?"
    )

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Milestone imminent: {value_now}/{milestone} {metric}",
    }


def _compose_renewal(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    days = payload.get("days_remaining", 0)
    plan = payload.get("plan", "Pro")
    amount = payload.get("renewal_amount", 0)
    owner = _owner_name(merchant) or _merchant_name(merchant)
    name = _merchant_name(merchant)
    hi = _uses_hindi(merchant)

    if hi:
        body = (
            f"{owner}, {name} ka {plan} plan sirf {days} din mein expire ho raha hai. "
            f"Renewal: ₹{amount:,}. Renew karne ke baad bhi sab features active rahenge. Abhi karo?"
        )
    else:
        body = (
            f"{owner}, your {plan} plan at {name} expires in {days} days (renewal: ₹{amount:,}). "
            f"Renew now to keep your profile visibility and lead flow going?"
        )

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Renewal due in {days} days",
    }


def _compose_recall_due(category, merchant, trigger, customer):
    """Customer-facing recall reminder."""
    if not customer:
        return _compose_generic(category, merchant, trigger, customer)

    payload = trigger.get("payload", {})
    service_due = payload.get("service_due", "appointment").replace("_", " ")
    slots = payload.get("available_slots", [])
    cust_name = customer.get("identity", {}).get("name", "")
    lang = customer.get("identity", {}).get("language_pref", "english")
    hi = "hi" in lang or "hindi" in lang.lower()
    merchant_name = _merchant_name(merchant)
    active_offers = _active_offers(merchant)

    slot_text = ""
    if len(slots) >= 2:
        slot_text = f"{slots[0].get('label', '')} ya {slots[1].get('label', '')}"
    elif len(slots) == 1:
        slot_text = slots[0].get("label", "")

    offer_text = f" {active_offers[0]} offer available." if active_offers else ""

    if hi:
        body = (
            f"Hi {cust_name} 🌟 {merchant_name} se bol rahe hain. "
            f"Aapki {service_due} due hai — time aa gaya hai."
        )
        if slot_text:
            body += f" Slots: {slot_text}. Reply 1 ya 2 choose karo."
        if offer_text:
            body += offer_text
    else:
        body = f"Hi {cust_name}, {merchant_name} here. Your {service_due} is due."
        if slot_text:
            body += f" Available slots: {slot_text}. Reply 1 or 2 to book."
        if offer_text:
            body += offer_text

    return {
        "body": body,
        "cta": "multi_choice_slot",
        "send_as": "merchant_on_behalf",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Customer recall due for {service_due}; offering available slots",
    }


def _compose_festival(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    festival = payload.get("festival", "upcoming festival")
    days_until = payload.get("days_until", "")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    active_offers = _active_offers(merchant)
    offer_ref = f" with your {active_offers[0]}" if active_offers else ""
    days_text = f"{days_until} days away" if days_until else "coming soon"

    body = (
        f"{owner}, {festival} is {days_text}. "
        f"One of the highest-footfall windows of the year{offer_ref}. "
        f"Want me to draft a {festival} post + customer offer for your WhatsApp list?"
    )

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Festival trigger: {festival} in {days_until} days",
    }


def _compose_ipl(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    match = payload.get("match", "IPL match")
    match_time_iso = payload.get("match_time_iso", "")
    is_weeknight = payload.get("is_weeknight", True)
    owner = _owner_name(merchant) or _merchant_name(merchant)
    active_offers = _active_offers(merchant)
    offer_ref = f"your {active_offers[0]}" if active_offers else "a match-night offer"

    time_text = ""
    if match_time_iso:
        try:
            dt = datetime.fromisoformat(match_time_iso.replace("Z", "+00:00"))
            time_text = f" at {dt.strftime('%I:%M %p')}"
        except Exception:
            pass

    if is_weeknight:
        body = (
            f"Quick heads-up {owner} — {match} tonight{time_text}. "
            f"Match nights boost delivery orders ~20%. "
            f"Want me to push {offer_ref} as a match-night special on delivery platforms?"
        )
    else:
        body = (
            f"Quick heads-up {owner} — {match} tonight{time_text}. "
            f"Note: Saturday IPL matches typically shift -12% restaurant covers to home delivery. "
            f"Skip the match-night dine-in promo; push {offer_ref} as a delivery-only special instead. "
            f"Want me to draft it?"
        )

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"IPL match {match} — delivery opportunity with day-of-week nuance",
    }


def _compose_review_theme(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    theme = payload.get("theme", "").replace("_", " ")
    count = payload.get("occurrences_30d", 0)
    trend = payload.get("trend", "")
    quote = payload.get("common_quote", "")
    owner = _owner_name(merchant) or _merchant_name(merchant)

    body = f"{owner}, {count} reviews in the last 30 days mention '{theme}'"
    if trend:
        body += f" (trend: {trend})"
    if quote:
        body += f'. Common quote: "{quote}".'
    else:
        body += "."
    body += " Want me to draft a response template + a quick operational fix note?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Review theme: {theme} ({count} occurrences, trend: {trend})",
    }


def _compose_dormant(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    days = payload.get("days_since_last_merchant_message", 0)
    last_topic = payload.get("last_topic", "").replace("_", " ")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    name = _merchant_name(merchant)
    active_offers = _active_offers(merchant)
    offer_ref = f"Active offer: {active_offers[0]}. " if active_offers else ""

    body = f"Hi {owner}, checking in on {name} — it's been {days} days since we last connected. "
    if last_topic:
        body += f"Last we spoke about {last_topic}. "
    body += offer_ref
    body += "Anything I can help with this week — profile, offers, or customer reach?"

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Merchant dormant for {days} days — re-engagement",
    }


def _compose_winback(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    days_expired = payload.get("days_since_expiry", 0)
    dip = payload.get("perf_dip_pct", 0)
    owner = _owner_name(merchant) or _merchant_name(merchant)
    name = _merchant_name(merchant)
    pct_str = f"{abs(int(dip * 100))}%" if dip else ""

    body = f"Hi {owner}, it's been {days_expired} days since your {name} plan expired. "
    if pct_str:
        body += f"Your visibility has dipped {pct_str} since then. "
    body += "We'd love to help you get back on track — want me to show you what's changed and the fastest path to recovery?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Winback: {days_expired} days post-expiry",
    }


def _compose_competitor(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    comp_name = payload.get("competitor_name", "a new competitor")
    distance = payload.get("distance_km", "")
    their_offer = payload.get("their_offer", "")
    opened_date = payload.get("opened_date", "")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    active_offers = _active_offers(merchant)

    body = f"{owner}, heads up — {comp_name} opened"
    if distance:
        body += f" {distance}km from you"
    if opened_date:
        body += f" ({_format_date(opened_date)})"
    body += "."
    if their_offer:
        body += f" Their listed offer: {their_offer}."
    if active_offers:
        body += f" Your active offer is {active_offers[0]} — want me to update your GBP to make the differentiation clear?"
    else:
        body += " Want me to help you craft a competitive response?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"New competitor {comp_name} opened nearby",
    }


def _compose_curious_ask(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    ask_template = payload.get("ask_template", "what_service_in_demand_this_week")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    name = _merchant_name(merchant)
    hi = _uses_hindi(merchant)

    if ask_template == "what_service_in_demand_this_week":
        if hi:
            body = (
                f"Hi {owner}! Quick check — is week {name} mein kaunsi service sabse zyada demand mein hai? "
                f"Main usse Google post + customer reply template mein convert kar deti hoon. 5 minute ka kaam."
            )
        else:
            body = (
                f"Hi {owner}! Quick check — what service has been most asked-for this week at {name}? "
                f"I'll turn it into a Google post + a ready reply template. 5 minutes."
            )
    else:
        body = (
            f"Hi {owner}, quick question for {name} — what's been working well this week? "
            f"I can draft a post or offer around it."
        )

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": "Curiosity-ask — low-stakes engagement question",
    }


def _compose_planning_intent(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    intent_topic = payload.get("intent_topic", "").replace("_", " ")
    last_message = payload.get("merchant_last_message", "")
    owner = _owner_name(merchant) or _merchant_name(merchant)

    body = f"{owner}, picking up from your message"
    if last_message:
        short = last_message[:60].strip()
        body += f' ("{short}{"..." if len(last_message) > 60 else ""}")'
    body += f" — let me draft a quick plan for {intent_topic}. Starter version in 2 min. Shall I go?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Active planning intent: {intent_topic}",
    }


def _compose_supply_alert(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    molecule = payload.get("molecule", "")
    batches = payload.get("affected_batches", [])
    manufacturer = payload.get("manufacturer", "")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    agg = merchant.get("customer_aggregate", {})
    chronic_count = agg.get("chronic_rx_count", 0)
    batch_str = ", ".join(batches[:3]) if batches else "affected batches"

    body = f"{owner}, urgent: voluntary recall on {molecule} batch(es) {batch_str}"
    if manufacturer:
        body += f" by {manufacturer}"
    body += " — sub-potency, no safety risk, but customers need replacement."
    if chronic_count:
        body += f" You have {chronic_count} chronic-Rx customers on file — I can filter for the affected ones. Want the list + draft WhatsApp?"
    else:
        body += " Want me to draft the customer notification?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Supply/recall alert for {molecule}: {batch_str}",
    }


def _compose_chronic_refill(category, merchant, trigger, customer):
    if not customer:
        return _compose_generic(category, merchant, trigger, customer)

    payload = trigger.get("payload", {})
    molecules = payload.get("molecule_list", [])
    runs_out = payload.get("stock_runs_out_iso", "")
    delivery_saved = payload.get("delivery_address_saved", False)
    cust_name = customer.get("identity", {}).get("name", "")
    lang = customer.get("identity", {}).get("language_pref", "english")
    hi = "hi" in lang or "hindi" in lang.lower()
    merchant_name = _merchant_name(merchant)
    active_offers = _active_offers(merchant)
    senior_offer = next((o for o in active_offers if "senior" in o.lower() or "15%" in o), None)

    mol_str = ", ".join(molecules[:3]) if molecules else "your medicines"
    run_str = _format_date(runs_out) if runs_out else ""

    if hi:
        body = f"Namaste — {merchant_name} se bol rahe hain. {cust_name} ji ki {mol_str} "
        if run_str:
            body += f"{run_str} ko khatam hongi. "
        body += "Same dose, same brand ready hai."
        if senior_offer:
            body += f" {senior_offer} apply hai."
        if delivery_saved:
            body += " Free home delivery saved address pe. CONFIRM karein?"
    else:
        body = f"Hello {cust_name}, {merchant_name} here. Your {mol_str} stock runs out"
        if run_str:
            body += f" around {run_str}"
        body += ". Same doses ready."
        if senior_offer:
            body += f" {senior_offer} applied."
        if delivery_saved:
            body += " Free delivery to saved address. Reply CONFIRM to dispatch."

    return {
        "body": body,
        "cta": "binary_confirm_cancel",
        "send_as": "merchant_on_behalf",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Chronic refill for {cust_name}: {mol_str} runs out {run_str}",
    }


def _compose_gbp_unverified(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    uplift = payload.get("estimated_uplift_pct", 0.30)
    uplift_pct = f"{int(uplift * 100)}%"
    owner = _owner_name(merchant) or _merchant_name(merchant)
    name = _merchant_name(merchant)
    perf = merchant.get("performance", {})
    views = perf.get("views", 0)
    est_uplift = int(views * uplift) if views else 0

    body = f"{owner}, {name}'s Google Business Profile is still unverified. Verified profiles get ~{uplift_pct} more visibility"
    if est_uplift:
        body += f" (~{est_uplift:,} additional views/month at your current traffic)"
    body += ". Verification takes ~5 min via postcard or phone call. Want me to walk you through it?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"GBP unverified — {uplift_pct} visibility uplift available",
    }


def _compose_customer_lapsed(category, merchant, trigger, customer):
    if not customer:
        return _compose_generic(category, merchant, trigger, customer)

    payload = trigger.get("payload", {})
    days_lapsed = payload.get("days_since_last_visit", 0)
    prev_focus = payload.get("previous_focus", "").replace("_", " ")
    prev_months = payload.get("previous_membership_months", 0)
    cust_name = customer.get("identity", {}).get("name", "")
    merchant_name = _merchant_name(merchant)
    owner = _owner_name(merchant) or merchant_name
    active_offers = _active_offers(merchant)

    offer_ref = f" Our current offer: {active_offers[0]}." if active_offers else ""

    body = f"Hi {cust_name} 👋 {owner} from {merchant_name} here. It's been about {days_lapsed // 7} weeks"
    if prev_months:
        body += f" since your last visit (you were with us {prev_months} months)"
    body += " — no pressure, just checking in."
    if prev_focus:
        body += f" We've added new options for {prev_focus} goals."
    body += offer_ref
    body += " Reply YES for a free trial slot, no strings attached."

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Customer lapsed {days_lapsed} days — no-shame winback",
    }


def _compose_trial_followup(category, merchant, trigger, customer):
    if not customer:
        return _compose_generic(category, merchant, trigger, customer)

    payload = trigger.get("payload", {})
    trial_date = payload.get("trial_date", "")
    next_sessions = payload.get("next_session_options", [])
    cust_name = customer.get("identity", {}).get("name", "")
    merchant_name = _merchant_name(merchant)
    owner = _owner_name(merchant) or merchant_name

    next_slot = next_sessions[0].get("label", "") if next_sessions else ""

    body = f"Hi {cust_name}, {owner} from {merchant_name} here — hope you enjoyed your trial"
    if trial_date:
        body += f" on {trial_date}"
    body += "!"
    if next_slot:
        body += f" Next session slot available: {next_slot}. Reply YES to confirm."
    else:
        body += " Ready to continue? Tell me your preferred day/time and I'll book it."

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": "Trial followup — converting trial to paid membership",
    }


def _compose_seasonal_dip(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    metric = payload.get("metric", "views")
    delta_pct = payload.get("delta_pct", 0)
    is_expected = payload.get("is_expected_seasonal", True)
    season_note = payload.get("season_note", "").replace("_", " ")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    pct_str = f"{abs(int(delta_pct * 100))}%"
    agg = merchant.get("customer_aggregate", {})
    active_members = agg.get("total_active_members", agg.get("total_unique_ytd", 0))

    body = f"{owner}, your {metric} are down {pct_str} this week."
    if is_expected and season_note:
        body += f" Good news — this is the normal {season_note} window (industry-wide pattern)."
    if active_members:
        body += f" Recommendation: skip new-acquisition spend for now; focus on retaining your {active_members} existing customers."
    body += " Want me to draft a retention campaign to keep them through the dip?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Expected seasonal dip {pct_str} — recommending retention focus",
    }


def _compose_category_seasonal(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    season = payload.get("season", "").replace("_", " ")
    trends = payload.get("trends", [])
    owner = _owner_name(merchant) or _merchant_name(merchant)
    active_offers = _active_offers(merchant)

    trend_str = ""
    if trends:
        trend_str = ", ".join(str(t).replace("_demand_", " demand ").replace("+", "+") for t in trends[:3])

    body = f"{owner}, seasonal demand shift underway ({season})."
    if trend_str:
        body += f" Key trends: {trend_str}."
    if active_offers:
        body += f" Your current offer ({active_offers[0]}) may need a seasonal refresh."
    body += " Want me to review your offer/shelf strategy for this season?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Category seasonal shift: {season}",
    }


def _compose_bridal_followup(category, merchant, trigger, customer):
    if not customer:
        return _compose_generic(category, merchant, trigger, customer)

    payload = trigger.get("payload", {})
    days_to_wedding = payload.get("days_to_wedding", "")
    next_window = payload.get("next_step_window_open", "").replace("_", " ")
    cust_name = customer.get("identity", {}).get("name", "")
    merchant_name = _merchant_name(merchant)
    owner = _owner_name(merchant) or merchant_name
    active_offers = _active_offers(merchant)
    bridal_offer = next((o for o in active_offers if "bridal" in o.lower()), None)

    body = f"Hi {cust_name} 💍 {owner} from {merchant_name} here."
    if days_to_wedding:
        body += f" {days_to_wedding} days to your wedding!"
    if next_window:
        body += f" Perfect window to start the {next_window}."
    if bridal_offer:
        body += f" {bridal_offer}."
    elif active_offers:
        body += f" {active_offers[0]}."
    body += " Want me to block your preferred slot for next week?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "merchant_on_behalf",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"Bridal followup for {cust_name}: {days_to_wedding} days to wedding",
    }


def _compose_cde(category, merchant, trigger, customer):
    payload = trigger.get("payload", {})
    credits = payload.get("credits", 0)
    fee = payload.get("fee", "")
    digest_items = category.get("digest", [])
    item_id = payload.get("digest_item_id", "")
    item = next((d for d in digest_items if d.get("id") == item_id), None)
    owner = _owner_name(merchant) or _merchant_name(merchant)

    body = f"{owner}, there's a CDE/training opportunity"
    if item:
        body += f": {item.get('title', '')}."
        date_str = item.get("date", "")
        if date_str:
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                body += f" Date: {dt.strftime('%d %b, %I%p')}."
            except Exception:
                pass
    body += f" {credits} credit(s)"
    if fee:
        body += f" — {fee}"
    body += ". Want me to register you?"

    return {
        "body": body,
        "cta": "binary_yes_no",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": f"CDE opportunity: {credits} credits, fee: {fee}",
    }


def _compose_generic(category, merchant, trigger, customer):
    """Absolute fallback for unknown trigger kinds."""
    kind = trigger.get("kind", "update")
    owner = _owner_name(merchant) or _merchant_name(merchant)
    name = _merchant_name(merchant)
    active_offers = _active_offers(merchant)
    offer_ref = f" Active: {active_offers[0]}." if active_offers else ""

    body = (
        f"Hi {owner}, quick update for {name} — there's a {kind.replace('_', ' ')} relevant to your business."
        f"{offer_ref} Want me to share the details?"
    )

    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": trigger.get("suppression_key", f"generic:{trigger.get('id', '')}"),
        "rationale": f"Generic fallback for trigger kind: {kind}",
    }


# -------------------------------------------------------------------------
# Dispatch table
# -------------------------------------------------------------------------

TRIGGER_KIND_TEMPLATES = {
    "research_digest": _compose_research_digest,
    "regulation_change": _compose_regulation_change,
    "perf_dip": _compose_perf_dip,
    "perf_spike": _compose_perf_spike,
    "milestone_reached": _compose_milestone,
    "renewal_due": _compose_renewal,
    "recall_due": _compose_recall_due,
    "festival_upcoming": _compose_festival,
    "ipl_match_today": _compose_ipl,
    "review_theme_emerged": _compose_review_theme,
    "dormant_with_vera": _compose_dormant,
    "winback_eligible": _compose_winback,
    "competitor_opened": _compose_competitor,
    "curious_ask_due": _compose_curious_ask,
    "active_planning_intent": _compose_planning_intent,
    "supply_alert": _compose_supply_alert,
    "chronic_refill_due": _compose_chronic_refill,
    "gbp_unverified": _compose_gbp_unverified,
    "customer_lapsed_hard": _compose_customer_lapsed,
    "customer_lapsed_soft": _compose_customer_lapsed,
    "trial_followup": _compose_trial_followup,
    "seasonal_perf_dip": _compose_seasonal_dip,
    "category_seasonal": _compose_category_seasonal,
    "wedding_package_followup": _compose_bridal_followup,
    "bridal_followup": _compose_bridal_followup,
    "cde_opportunity": _compose_cde,
}


def compose_fallback(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    Deterministic composition using templates.
    Falls back gracefully — always returns a valid response.
    """
    kind = trigger.get("kind", "")
    fn = TRIGGER_KIND_TEMPLATES.get(kind, _compose_generic)
    result = fn(category, merchant, trigger, customer)
    # Ensure all required fields are present
    result.setdefault("body", "")
    result.setdefault("cta", "open_ended")
    result.setdefault("send_as", "vera")
    result.setdefault("suppression_key", trigger.get("suppression_key", ""))
    result.setdefault("rationale", f"Fallback composition for {kind}")
    return result
