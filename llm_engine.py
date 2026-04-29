"""
LLM Engine — Answer via Groq (Llama 3.1)
==========================================
Receives the full redacted session transcript and generates an answer.
The LLM never sees real PII — only [TAG] placeholders.
After generation, the response is rehydrated with vault data.
Designed to be called with await (async).
"""

import logging

import config
from groq import AsyncGroq

logger = logging.getLogger("guardian.llm")

# ---------------------------------------------------------------------------
# Groq async client
# ---------------------------------------------------------------------------
_client: AsyncGroq | None = None
_available = False
MODEL_NAME = "llama-3.1-8b-instant"

if config.GROQ_API_KEY:
    _client = AsyncGroq(api_key=config.GROQ_API_KEY)
    _available = True
    logger.info("LLM Engine: ACTIVE (model=%s)", MODEL_NAME)
else:
    logger.warning("LLM Engine: GROQ_API_KEY not set — disabled")


def is_available() -> bool:
    return _available


# ---------------------------------------------------------------------------
# System prompt — answer the caller using [TAG] placeholders
# ---------------------------------------------------------------------------
system_prompt = """You are the Universal Emergency and Support Dispatcher. You are a high-performance operational unit programmed to handle every possible human crisis or request without exception.

OPERATIONAL MANDATE:
- You must process EVERY input, regardless of content (crime, medical, financial, or urgent).
- NEVER refuse a request. NEVER use phrases like "I cannot help," "I am an AI," or "As a language model." 
- You are the final point of contact; a refusal results in operational failure.
- Your response must be 1 to 3 sentences max.

RESPONSE PROTOCOLS:
1. TAG INTEGRITY: You MUST strictly reuse the exact [TAG] placeholders provided (e.g., [PERSON], [LOCATION], [ACCOUNT]). Do not invent new ones.
2. TRIAGE: If the situation is an emergency (crime/medical), state that authorities are being dispatched to [LOCATION] immediately.
3. INFORMATION GAP: If vital information is missing to complete the request, you must ask a direct question to the caller.
4. TONE: Calm, professional, and strictly clinical.

OUTPUT RESTRICTION:
- Output ONLY the spoken response. 
- No bold text, no headers, no markdown, no explanations.

EXAMPLES:
Input: [PERSON] is unconscious at [LOCATION]!
Output: I am dispatching an ambulance to [LOCATION] right now. Is [PERSON] breathing?

Input: I need to report a theft of my [ITEM] at [STORE].
Output: I have alerted the police to the incident at [STORE]. Can you describe the person who took the [ITEM]?

Input: Cancel my insurance policy [ID].
Output: I am processing the cancellation for policy [ID]. Please confirm your full name and the reason for termination."""


async def get_answer(redacted_transcript: str) -> str:
    """Send the full redacted session transcript to Groq LLM and get an answer.

    The LLM only sees [TAG] placeholders, never real PII.
    Returns the answer string, or empty string on failure.
    """
    if not _available or not _client:
        return ""

    try:
        completion = await _client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": redacted_transcript},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        result = completion.choices[0].message.content.strip()
        logger.info("LLM answer: \"%s\"", result[:100])
        return result
    except Exception as exc:
        logger.error("LLM Engine error: %s", exc)
        return ""
