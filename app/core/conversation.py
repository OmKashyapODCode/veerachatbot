"""
Conversation state manager — tracks all in-flight conversations.
Handles auto-reply detection, intent classification, and turn management.
"""

import threading
import re
from typing import Any, Dict, List, Optional


# Auto-reply phrases to detect WhatsApp Business canned messages
AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"thanks for contacting",
    r"thank you for reaching out",
    r"our team will (respond|get back|reply) (to you )?(shortly|soon)",
    r"we will (get back|respond|reply) (to you )?(shortly|soon|within)",
    r"automated (message|assistant|reply|response)",
    r"this is an automated",
    r"i am an automated",
    r"currently (unavailable|away|busy|out of office)",
    r"business hours.*we'll get back",
    r"out of office",
    r"will respond within \d+",
    r"aapki jaankari ke liye.*shukriya",
    r"main.*automated",
    r"team tak pahuncha",
    r"working hours.*reply",
]

_AUTO_REPLY_RE = [re.compile(p, re.IGNORECASE) for p in AUTO_REPLY_PATTERNS]


# Intent patterns
INTENT_YES = [
    r"^(yes|ya|yep|yup|ok|okay|sure|alright|fine|haan|haa|hann|haa ji|ji ha|ji haan|theek hai)[\s!.]*$",
    r"(let['']?s do it|let's go|go ahead|proceed|do it|send it|go for it|chalo karte hain|karo|kar do)",
    r"(sounds good|looks good|good idea|great idea|perfect|awesome)",
    r"(confirm|confirmed|approved|approve)",
]
INTENT_NO = [
    r"^(no|nope|nahi|nahi ji|nahin|not interested|no thanks|stop|dont|don't)[\s!.]*$",
    r"(not interested|stop messaging|don't message|stop sending|not now|not right now|maybe later)",
    r"(unsubscribe|remove me|opt out|opt-out)",
]
INTENT_WAIT = [
    r"(give me (some |a bit of )?time|let me think|i['']ll (get back|think about)|will reply later|baad mein|later|thodi der)",
    r"(busy right now|call you later|not free|kal batata|kal bata)",
]
INTENT_HOSTILE = [
    r"(stop (bothering|messaging|contacting|harassing)|spam|useless|waste of time|annoying|irritating|don't bother)",
    r"(stupid|idiot|useless|bakwaas|band karo|chup karo|bhago|niklo)",
]
INTENT_QUESTION = [
    r"(what is|what's|kya hai|kaise|how does|tell me more|explain|more info|details|can you tell)",
    r"(pricing|price|cost|kitna|how much|how many|fee|charges)",
]
INTENT_COMMIT = [
    r"(ok(ay)?[,.]?\s*(let'?s?|we can|sure|yes))?\s*(do it|go|proceed|start|begin|start|setup|set up|schedule)",
    r"(what['']?s next|what do (i|we) do next|next step|aage kya|ab kya)",
]

_INTENT_YES_RE = [re.compile(p, re.IGNORECASE) for p in INTENT_YES]
_INTENT_NO_RE = [re.compile(p, re.IGNORECASE) for p in INTENT_NO]
_INTENT_WAIT_RE = [re.compile(p, re.IGNORECASE) for p in INTENT_WAIT]
_INTENT_HOSTILE_RE = [re.compile(p, re.IGNORECASE) for p in INTENT_HOSTILE]
_INTENT_QUESTION_RE = [re.compile(p, re.IGNORECASE) for p in INTENT_QUESTION]
_INTENT_COMMIT_RE = [re.compile(p, re.IGNORECASE) for p in INTENT_COMMIT]


def classify_intent(message: str) -> str:
    """Classify merchant/customer message intent."""
    msg = message.strip()

    # Check hostile first (takes priority)
    for pat in _INTENT_HOSTILE_RE:
        if pat.search(msg):
            return "hostile"

    # Check explicit commit/action
    for pat in _INTENT_COMMIT_RE:
        if pat.search(msg):
            return "commit"

    # Check yes/positive
    for pat in _INTENT_YES_RE:
        if pat.search(msg):
            return "yes"

    # Check no/negative
    for pat in _INTENT_NO_RE:
        if pat.search(msg):
            return "no"

    # Check wait
    for pat in _INTENT_WAIT_RE:
        if pat.search(msg):
            return "wait"

    # Check question
    for pat in _INTENT_QUESTION_RE:
        if pat.search(msg):
            return "question"

    return "unknown"


def is_auto_reply(message: str) -> bool:
    """Return True if the message looks like a WhatsApp Business auto-reply."""
    msg = message.strip()
    for pat in _AUTO_REPLY_RE:
        if pat.search(msg):
            return True
    return False


class ConversationState:
    """Per-conversation mutable state."""

    __slots__ = (
        "conversation_id",
        "merchant_id",
        "customer_id",
        "trigger_id",
        "turn_number",
        "is_active",
        "bot_messages",        # list of bot message bodies (for anti-repetition)
        "merchant_messages",   # list of merchant message bodies
        "auto_reply_count",    # consecutive auto-replies detected
        "auto_reply_messages", # set of distinct auto-reply bodies seen
        "last_intent",
        "metadata",            # arbitrary extra dict (category_slug, etc.)
    )

    def __init__(
        self,
        conversation_id: str,
        merchant_id: str,
        customer_id: Optional[str],
        trigger_id: Optional[str],
    ):
        self.conversation_id = conversation_id
        self.merchant_id = merchant_id
        self.customer_id = customer_id
        self.trigger_id = trigger_id
        self.turn_number = 1
        self.is_active = True
        self.bot_messages: List[str] = []
        self.merchant_messages: List[str] = []
        self.auto_reply_count = 0
        self.auto_reply_messages: set = set()
        self.last_intent: Optional[str] = None
        self.metadata: Dict[str, Any] = {}

    def record_bot_message(self, body: str):
        self.bot_messages.append(body)

    def record_merchant_message(self, message: str) -> str:
        """Record merchant message, detect auto-reply and intent. Returns detected intent."""
        self.merchant_messages.append(message)

        if is_auto_reply(message):
            self.auto_reply_messages.add(message.strip())
            return "auto_reply"

        intent = classify_intent(message)
        self.last_intent = intent
        return intent

    def is_repeated_bot_message(self, body: str) -> bool:
        return body.strip() in (m.strip() for m in self.bot_messages)

    def close(self):
        self.is_active = False


class ConversationManager:
    """Thread-safe manager for all conversations."""

    def __init__(self):
        self._conversations: Dict[str, ConversationState] = {}
        self._lock = threading.RLock()
        self._used_conv_ids: set = set()  # for tick dedup
        # Global per-merchant auto-reply counter (survives across conversation IDs)
        # Key: merchant_id, Value: int (consecutive auto-reply count)
        self._merchant_auto_reply_count: Dict[str, int] = {}

    def get_or_create(
        self,
        conversation_id: str,
        merchant_id: str,
        customer_id: Optional[str] = None,
        trigger_id: Optional[str] = None,
    ) -> ConversationState:
        with self._lock:
            if conversation_id not in self._conversations:
                self._conversations[conversation_id] = ConversationState(
                    conversation_id=conversation_id,
                    merchant_id=merchant_id,
                    customer_id=customer_id,
                    trigger_id=trigger_id,
                )
                self._used_conv_ids.add(conversation_id)
            return self._conversations[conversation_id]

    def get(self, conversation_id: str) -> Optional[ConversationState]:
        with self._lock:
            return self._conversations.get(conversation_id)

    def exists(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._conversations

    def is_used(self, conversation_id: str) -> bool:
        """True if this conv_id was ever used (even if now closed)."""
        with self._lock:
            return conversation_id in self._used_conv_ids

    def increment_merchant_auto_reply(self, merchant_id: str) -> int:
        """
        Increment and return the global auto-reply counter for a merchant.
        This persists across different conversation_ids (since judge uses different conv IDs per auto-reply test).
        """
        with self._lock:
            count = self._merchant_auto_reply_count.get(merchant_id, 0) + 1
            self._merchant_auto_reply_count[merchant_id] = count
            return count

    def reset_merchant_auto_reply(self, merchant_id: str):
        """Reset the auto-reply counter when merchant sends a real message."""
        with self._lock:
            self._merchant_auto_reply_count.pop(merchant_id, None)

    def get_merchant_auto_reply_count(self, merchant_id: str) -> int:
        with self._lock:
            return self._merchant_auto_reply_count.get(merchant_id, 0)

    def wipe(self):
        with self._lock:
            self._conversations.clear()
            self._used_conv_ids.clear()
            self._merchant_auto_reply_count.clear()


# -------------------------------------------------------------------------
# Singletons
# -------------------------------------------------------------------------

_conversation_manager = ConversationManager()


def get_conversation_manager() -> ConversationManager:
    return _conversation_manager
