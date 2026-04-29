"""
Zero Trust Vault — Secure PII Storage & Rehydration
=====================================================
Stores redacted entities in a local JSON vault with session IDs.
Supports rehydration: replacing [TAG] placeholders back with real values.

Architecture:
  - On redaction, entities are stored locally with a session ID
  - The redacted (safe) text can be sent to external LLMs / services
  - On return, the vault rehydrates the response with the original PII
  - PII never leaves the local server
"""

import json
import logging
import os
import uuid

logger = logging.getLogger("guardian.vault")


class ZeroTrustVault:
    def __init__(self, vault_file="secure_vault.json"):
        self.vault_file = vault_file
        if not os.path.exists(self.vault_file):
            with open(self.vault_file, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def secure_store(self, context, entities) -> str:
        """Store entities in the JSON vault. Returns a session ID."""
        session_id = str(uuid.uuid4())[:8]

        with open(self.vault_file, "r", encoding="utf-8") as f:
            vault_data = json.load(f)

        vault_data[session_id] = {
            "context": context,
            "entities": entities,
        }

        with open(self.vault_file, "w", encoding="utf-8") as f:
            json.dump(vault_data, f, indent=4, ensure_ascii=False)

        logger.info("Vault: Stored %d entities under session %s (context=%s)",
                     len(entities), session_id, context)

        return session_id

    def rehydrate(self, censored_text: str, session_id: str) -> str | None:
        """Replace [TAG] placeholders with real values from the vault.

        Returns the rehydrated text, or None if session not found.
        """
        try:
            with open(self.vault_file, "r", encoding="utf-8") as f:
                vault_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

        session = vault_data.get(session_id)
        if not session:
            return None

        entities = session.get("entities", [])
        return self._rehydrate_text(censored_text, entities)

    @staticmethod
    def _rehydrate_text(censored_text: str, entities: list[dict]) -> str:
        """Smart rehydration — handles LLM repeating tags.

        - If only 1 value for a tag: replace ALL occurrences (LLM may repeat it)
        - If multiple values: replace one-by-one in order
        - Fallback: any leftover tags get the last known value
        """
        rehydrated = censored_text

        # Group by label tag
        label_map: dict[str, list[str]] = {}
        for ent in entities:
            tag = f"[{ent['label']}]"
            if tag not in label_map:
                label_map[tag] = []
            label_map[tag].append(ent["text"])

        for tag, real_values in label_map.items():
            if len(real_values) == 1:
                # Single value — replace ALL occurrences
                rehydrated = rehydrated.replace(tag, real_values[0])
            else:
                # Multiple values — replace one-by-one in order
                for real_value in real_values:
                    rehydrated = rehydrated.replace(tag, real_value, 1)
                # Safety: if LLM added extra tags, fill with last known value
                rehydrated = rehydrated.replace(tag, real_values[-1])

        return rehydrated

    def rehydrate_with_entities(self, censored_text: str, entities: list[dict]) -> str:
        """Rehydrate directly with an entity list (no vault lookup needed)."""
        return self._rehydrate_text(censored_text, entities)

    def clear(self):
        """Wipe all stored sessions — called on each new recording."""
        with open(self.vault_file, "w", encoding="utf-8") as f:
            json.dump({}, f)
        logger.info("Vault: Cleared all sessions")

    def get_session(self, session_id: str) -> dict | None:
        """Retrieve session data from the vault."""
        try:
            with open(self.vault_file, "r", encoding="utf-8") as f:
                vault_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return vault_data.get(session_id)
