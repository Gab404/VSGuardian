"""
Secure Voice Guardian — Shared Configuration
=============================================
Entity definitions, context rules, and API config.
Imported by fastino_engine.py, vault_manager.py, etc.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# API Key Validation (SECURITY FIX: Fail-fast on missing credentials)
# ---------------------------------------------------------------------------
def _validate_required_env(key: str, description: str) -> str:
    """Validate and retrieve a required environment variable. Fails fast if missing."""
    value = os.getenv(key, "").strip()
    if not value:
        print(f"ERROR: Required environment variable '{key}' is not set ({description})")
        print(f"Please configure {key} in your .env file")
        sys.exit(1)
    return value

def _validate_optional_env(key: str, default: str = "") -> str:
    """Retrieve an optional environment variable with a default fallback."""
    return os.getenv(key, default).strip()

# ---------------------------------------------------------------------------
# Fastino / Pioneer API
# ---------------------------------------------------------------------------
FASTINO_API_KEY = _validate_optional_env("FASTINO_API_KEY")
FASTINO_API_URL = _validate_optional_env("FASTINO_API_URL", "https://api.pioneer.ai/inference")
MODEL_ID = _validate_optional_env("FASTINO_MODEL_ID", "fastino/gliner2-base-v1")

GRADIUM_API_KEY = _validate_optional_env("GRADIUM_API_KEY")
GRADIUM_TTS_URL = _validate_optional_env("GRADIUM_TTS_URL", "")
GRADIUM_WS_URL = _validate_optional_env("GRADIUM_WS_URL", "wss://api.gradium.ai/api/speech/asr")
GRADIUM_MODEL_ID = _validate_optional_env("GRADIUM_MODEL_ID", "gradium/stt-fr-high-quality")
# ---------------------------------------------------------------------------
# Groq LLM (for action summaries)
# ---------------------------------------------------------------------------
GROQ_API_KEY = _validate_optional_env("GROQ_API_KEY")

# ---------------------------------------------------------------------------
# Base entities — always detected regardless of context
# ---------------------------------------------------------------------------
TARGET_ENTITIES = [
    "person", 
    "location", 
    "organization", 
    "date", 
    "age",
    "password",          # Toujours censurer un mot de passe
    "social security number" # Extrêmement critique
]

# ---------------------------------------------------------------------------
# Context rules — extra entities added when keywords are detected
# ---------------------------------------------------------------------------
CONTEXT_RULES = {
    "FINANCE": {
        "mots_cles": ["banque", "compte", "virement", "payer", "euros", "solde", "carte", "iban"],
        "labels_extra": ["iban", "bank account number", "swift code", "tax id"]
    },
    "MEDICAL": {
        "mots_cles": ["docteur", "médecin", "hôpital", "mal", "douleur", "ordonnance", "clinique", "traitement"],
        "labels_extra": ["disease", "medication", "symptom", "blood type"]
    },
    "CYBER": {
        "mots_cles": ["connexion", "ordinateur", "bug", "hacker", "pirate", "site", "écran"],
        "labels_extra": ["ip address", "username", "pin code", "url"]
    },
    "HR_LEGAL": {
        "mots_cles": ["contrat", "embauche", "licenciement", "loi", "avocat", "justice"],
        "labels_extra": ["profession", "nationality", "id number"]
    }
}