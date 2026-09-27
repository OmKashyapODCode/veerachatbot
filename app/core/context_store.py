"""
In-memory context store — thread-safe storage for all 4 context types.
Implements versioning: same version = idempotent, higher version = replace, lower = reject.
"""

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


class ContextEntry:
    __slots__ = ("version", "payload", "stored_at")

    def __init__(self, version: int, payload: Dict[str, Any], stored_at: str):
        self.version = version
        self.payload = payload
        self.stored_at = stored_at


class ContextStore:
    """
    Thread-safe in-memory store for all 4 context scopes.
    Key: (scope, context_id)
    Value: ContextEntry(version, payload, stored_at)
    """

    def __init__(self):
        self._store: Dict[Tuple[str, str], ContextEntry] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def push(
        self,
        scope: str,
        context_id: str,
        version: int,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Store or update a context.

        Returns:
            dict with keys: accepted (bool), and either ack_id/stored_at or reason/current_version.
        """
        if scope not in VALID_SCOPES:
            return {"accepted": False, "reason": "invalid_scope", "details": f"scope must be one of {VALID_SCOPES}"}

        key = (scope, context_id)
        stored_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._lock:
            existing = self._store.get(key)

            if existing is not None:
                if existing.version > version:
                    # Already have a newer version → reject
                    return {
                        "accepted": False,
                        "reason": "stale_version",
                        "current_version": existing.version,
                    }
                if existing.version == version:
                    # Idempotent re-push → accept silently
                    return {
                        "accepted": True,
                        "ack_id": f"ack_{context_id}_v{version}",
                        "stored_at": existing.stored_at,
                    }

            # New or higher version → store atomically
            self._store[key] = ContextEntry(version=version, payload=payload, stored_at=stored_at)

        return {
            "accepted": True,
            "ack_id": f"ack_{context_id}_v{version}",
            "stored_at": stored_at,
        }

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, scope: str, context_id: str) -> Optional[Dict[str, Any]]:
        """Return the payload dict for (scope, context_id), or None."""
        with self._lock:
            entry = self._store.get((scope, context_id))
            return entry.payload if entry else None

    def get_entry(self, scope: str, context_id: str) -> Optional[ContextEntry]:
        with self._lock:
            return self._store.get((scope, context_id))

    def counts(self) -> Dict[str, int]:
        """Return {scope: count} for health reporting."""
        with self._lock:
            counts: Dict[str, int] = {s: 0 for s in VALID_SCOPES}
            for (scope, _) in self._store:
                counts[scope] = counts.get(scope, 0) + 1
            return counts

    def all_by_scope(self, scope: str) -> Dict[str, Dict[str, Any]]:
        """Return all payloads for a given scope, keyed by context_id."""
        with self._lock:
            return {
                cid: entry.payload
                for (s, cid), entry in self._store.items()
                if s == scope
            }

    def wipe(self):
        """Clear all stored contexts (called on teardown)."""
        with self._lock:
            self._store.clear()


# Singleton instance
_context_store = ContextStore()


def get_context_store() -> ContextStore:
    return _context_store
