"""
Fastino Privacy Shield — Context-Aware PII Redaction Engine
============================================================
Uses Pioneer API (GLiNER model) + regex patterns to detect and redact
personally identifiable information from transcription text.

Context-aware: detects CRIME / MEDICAL / FINANCE scenarios and adds
domain-specific entity types automatically.

Optimized:
  - httpx AsyncClient for non-blocking API calls (FastAPI friendly)
  - Pre-compiled regex patterns for speed
  - Secure UUID-based masking tokens to prevent collisions
  - Sorted entity replacement (longest first) to prevent partial sub-string masking
  - STOP_WORDS anti-hallucination filter
  - Smart word-boundary regex replacement (handles $, symbols)
"""

import logging
import re
import uuid

import config
import httpx

import hashlib
from functools import lru_cache

logger = logging.getLogger("guardian.fastino")

# ---------------------------------------------------------------------------
# Anti-hallucination: common words GLiNER might incorrectly flag as entities
# ---------------------------------------------------------------------------
STOP_WORDS = {
    # Pronouns
    "i",
    "me",
    "my",
    "mine",
    "myself",
    "you",
    "your",
    "yours",
    "yourself",
    "he",
    "him",
    "his",
    "himself",
    "she",
    "her",
    "hers",
    "herself",
    "it",
    "its",
    "itself",
    "we",
    "us",
    "our",
    "ours",
    "ourselves",
    "they",
    "them",
    "their",
    "theirs",
    "themselves",
    # Determiners / articles
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "some",
    "any",
    "no",
    "every",
    "each",
    "all",
    "both",
    "few",
    "many",
    "much",
    "more",
    "most",
    "other",
    "another",
    # Prepositions
    "of",
    "to",
    "in",
    "for",
    "on",
    "at",
    "by",
    "with",
    "from",
    "about",
    "into",
    "through",
    "during",
    "before",
    "after",
    "between",
    "without",
    "under",
    "over",
    "above",
    "below",
    "against",
    "along",
    "around",
    "behind",
    "beside",
    "down",
    "inside",
    "near",
    "off",
    "onto",
    "out",
    "outside",
    "past",
    "since",
    "toward",
    "until",
    "upon",
    "within",
    "up",
    # Conjunctions / question words
    "and",
    "but",
    "or",
    "nor",
    "so",
    "yet",
    "because",
    "although",
    "while",
    "if",
    "when",
    "where",
    "how",
    "what",
    "which",
    "who",
    "whom",
    "whose",
    "than",
    "whether",
    # Common verbs (never PII)
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "having",
    "do",
    "does",
    "did",
    "doing",
    "done",
    "will",
    "would",
    "shall",
    "should",
    "can",
    "could",
    "may",
    "might",
    "must",
    "get",
    "got",
    "go",
    "goes",
    "going",
    "gone",
    "went",
    "take",
    "took",
    "taken",
    "make",
    "made",
    "making",
    "come",
    "came",
    "coming",
    "say",
    "said",
    "says",
    "know",
    "knew",
    "known",
    "see",
    "saw",
    "seen",
    "want",
    "wanted",
    "give",
    "gave",
    "given",
    "tell",
    "told",
    "call",
    "called",
    "calling",
    "try",
    "tried",
    "ask",
    "asked",
    "need",
    "needed",
    "feel",
    "felt",
    "keep",
    "kept",
    "let",
    "leave",
    "left",
    "put",
    "run",
    "ran",
    "think",
    "thought",
    "send",
    "sent",
    "help",
    "helped",
    "hear",
    "heard",
    "die",
    "died",
    "dying",
    "pay",
    "paid",
    "stop",
    "stopped",
    "start",
    "started",
    "look",
    "looked",
    "bring",
    "brought",
    "hold",
    "held",
    "stand",
    "stood",
    "sit",
    "sat",
    "set",
    "show",
    "showed",
    "move",
    "moved",
    "live",
    "lived",
    "work",
    "worked",
    # Adverbs
    "not",
    "very",
    "really",
    "just",
    "also",
    "too",
    "here",
    "there",
    "now",
    "then",
    "still",
    "already",
    "always",
    "never",
    "often",
    "ever",
    "again",
    "only",
    "even",
    "well",
    "back",
    "soon",
    "maybe",
    "perhaps",
    "quite",
    "rather",
    # Common adjectives (never PII)
    "big",
    "small",
    "old",
    "new",
    "good",
    "bad",
    "great",
    "first",
    "last",
    "long",
    "little",
    "own",
    "right",
    "wrong",
    "same",
    "able",
    "real",
    "sure",
    "true",
    "next",
    "high",
    "low",
    # Greetings / responses
    "hello",
    "hi",
    "hey",
    "goodbye",
    "bye",
    "yes",
    "no",
    "ok",
    "okay",
    "yeah",
    "yep",
    "nope",
    "please",
    "thank",
    "thanks",
    "sorry",
    "sir",
    "madam",
    # Common nouns (not PII)
    "name",
    "years",
    "old",
    "age",
    "time",
    "day",
    "number",
    # Numbers as words
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    # French filler
    "bonjour",
    "salut",
    "merci",
    "oui",
    "non",
}

# ---------------------------------------------------------------------------
# Pre-compiled Regex Patterns (Saves CPU cycles on every call)
# ---------------------------------------------------------------------------
REGEX_PATTERNS = {
    "EMAIL": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    "PHONE_FR": re.compile(r"(?:(?:\+|00)33|0)\s*[1-9](?:[\s.-]*\d{2}){4}"),
    "PHONE_US": re.compile(r"\+?1?\s*\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
}


# ---------------------------------------------------------------------------
# Context detection — supports multiple simultaneous contexts
# ---------------------------------------------------------------------------
# def _detect_contexts(text: str) -> list[str]:
#     """Detect which context rules apply based on keywords in the text."""
#     text_lower = text.lower()
#     detected = []
#     for ctx_name, rules in config.CONTEXT_RULES.items():
#         if any(kw in text_lower for kw in rules["mots_cles"]):
#             detected.append(ctx_name)
#     return detected


@lru_cache(maxsize=256)
def _detect_contexts_cached(text_hash: str, text_lower: str) -> tuple[str, ...]:
    detected = []
    for ctx_name, rules in config.CONTEXT_RULES.items():
        if any(kw in text_lower for kw in rules["mots_cles"]):
            detected.append(ctx_name)
    return tuple(detected)


def _detect_contexts(text: str) -> list[str]:
    text_lower = text.lower()
    text_hash = hashlib.md5(text_lower.encode()).hexdigest()
    return list(_detect_contexts_cached(text_hash, text_lower))


def _build_entity_list(text: str) -> tuple[list[str], list[str]]:
    """Build the full entity list: base + context-specific extras.

    Returns (deduplicated_entities, detected_contexts).
    """
    entities = config.TARGET_ENTITIES.copy()
    contexts = _detect_contexts(text)
    for ctx in contexts:
        entities.extend(config.CONTEXT_RULES[ctx]["labels_extra"])
        logger.info(
            "Fastino: Context %s detected — added %d extra entity types",
            ctx,
            len(config.CONTEXT_RULES[ctx]["labels_extra"]),
        )
    # Deduplicate while preserving order
    entities = list(dict.fromkeys(entities))
    return entities, contexts


class FastinoPrivacyShield:
    """Detects and redacts PII using regex + Pioneer GLiNER API."""

    def __init__(self):
        self.api_key = config.FASTINO_API_KEY
        self.api_url = config.FASTINO_API_URL
        self.model_id = config.MODEL_ID
        self._available = bool(self.api_key)

        # Persistent ASYNC session with SSL verification enabled (SECURITY FIX)
        self._client = httpx.AsyncClient(verify=True)
        self._client.headers.update(
            {
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
            }
        )

        if not self._available:
            logger.warning("Fastino: FASTINO_API_KEY not set — PII filtering disabled")
        else:
            logger.info("Fastino: Privacy shield ready (model=%s)", self.model_id)

    def is_available(self) -> bool:
        return self._available

    # ⚠️ Modifié en 'async def' pour ne pas bloquer FastAPI
    async def analyze_and_redact(self, text: str) -> tuple[str, list[dict], list[str]]:
        """Redact PII from text asynchronously.

        Returns (redacted_text, entities_found, detected_contexts).
        detected_contexts is a list like ["CRIME", "MEDICAL", "FINANCE"].
        """
        if not text or not text.strip():
            return text, [], []

        redacted_text = text
        found_entities: list[dict] = []
        mask_map: dict[str, str] = {}

        # Build context-aware entity list
        entities, detected_contexts = _build_entity_list(text)

        # ==================================================
        # Layer 1: REGEX — emails, phones, credit cards
        # These are caught first with secure UUID mask tokens
        # so GLiNER doesn't interfere with them.
        # ==================================================

        # def _apply_regex_mask(pattern_key: str, label: str):
        #     nonlocal redacted_text
        #     for match in REGEX_PATTERNS[pattern_key].finditer(redacted_text):
        #         val = match.group()
        #         if "__FSTNO_MSK_" in val:
        #             continue
        #         # Secure Masking Token
        #         token = f" __FSTNO_MSK_{uuid.uuid4().hex[:8]}__ "
        #         mask_map[token.strip()] = f"[{label}]"
        #         found_entities.append({"label": label, "text": val})
        #         redacted_text = redacted_text.replace(val, token)

        def _apply_regex_mask(pattern_key: str, label: str):
            nonlocal redacted_text
            for match in REGEX_PATTERNS[pattern_key].finditer(redacted_text):
                val = match.group()
                if "__FSTNO_MSK_" in val:
                    continue
                token = f"__FSTNO_MSK_{uuid.uuid4().hex[:8]}__"
                mask_map[token] = f"[{label}]"
                found_entities.append({"label": label, "text": val})
                redacted_text = re.sub(re.escape(val), token, redacted_text, count=1)

        _apply_regex_mask("EMAIL", "EMAIL")
        _apply_regex_mask("PHONE_FR", "PHONE")
        _apply_regex_mask("PHONE_US", "PHONE")
        _apply_regex_mask("CREDIT_CARD", "CREDIT CARD")

        # ==================================================
        # Layer 2: Pioneer API (GLiNER NER model)
        #          Context-aware + anti-hallucination
        # ==================================================
        if self._available:
            try:
                payload = {
                    "model_id": self.model_id,
                    "text": redacted_text,
                    "schema": {"entities": entities},
                }

                # Appels asynchrones avec await
                response = await self._client.post(
                    self.api_url,
                    json=payload,
                    timeout=5.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    entities_dict = data.get("result", {}).get("entities", {})

                    # Aplatir le dictionnaire et filtrer
                    # Aplatir le dictionnaire et filtrer
                    gliner_items = []
                    for label, found_items in entities_dict.items():
                        for item in found_items:
                            clean_item = str(item).strip()

                            # 1. Filtres de base (ce que tu avais déjà)
                            if not clean_item or "__FSTNO_MSK_" in clean_item:
                                continue
                            if len(clean_item) <= 1:
                                continue
                            if clean_item.lower() in STOP_WORDS:
                                continue
                            if clean_item.isdigit() and len(clean_item) <= 4:
                                continue

                            # ==========================================
                            # 🛡️ NOUVEAUX FILTRES AVANCÉS (HEURISTIQUES)
                            # ==========================================
                            lbl = label.upper()

                            # 2. Règle de longueur stricte pour Noms et Lieux
                            # En français, un nom de famille, une entreprise ou une ville de 2 lettres est très rare.
                            if lbl in ["PERSON", "LOCATION", "CITY", "ORG", "COMPANY"]:
                                if len(clean_item) <= 2:
                                    continue

                            # 3. Règle de la Casse (Majuscule)
                            # Gradium STT met généralement des majuscules aux noms propres.
                            # Si GLiNER tague un mot tout en minuscules comme une PERSONNE, c'est souvent une erreur (ex: "il", "le").
                            if lbl == "PERSON":
                                if clean_item.islower():
                                    continue

                            # 4. Anti-Faux-Positifs sur les Dates et Adresses
                            # Une "DATE" valide contient presque toujours des séparateurs ou des lettres (15/03 ou 15 mars).
                            # Si l'IA tague juste "15" comme une DATE, on rejette.
                            if lbl in ["DATE", "ADDRESS", "LOCATION"]:
                                if clean_item.isdigit():
                                    continue

                            # 5. Mots d'arrêt étendus dynamiquement
                            # Rejeter les verbes courants conjugués que GLiNER confond avec des noms
                            # extended_stops = {
                            #     "suis",
                            #     "est",
                            #     "sommes",
                            #     "êtes",
                            #     "sont",
                            #     "ai",
                            #     "as",
                            #     "a",
                            #     "avons",
                            #     "avez",
                            #     "ont",
                            #     "vais",
                            #     "vas",
                            #     "va",
                            #     "allons",
                            #     "allez",
                            #     "vont",
                            #     "faire",
                            #     "dire",
                            # }
                            # if clean_item.lower() in extended_stops:
                            #     continue

                            gliner_items.append((label, clean_item))

                    # Optimisation: Trier par longueur décroissante
                    # Empêche le remplacement partiel (ex: masque "Jean" avant "Jean Dupont")
                    # gliner_items.sort(key=lambda x: len(x[1]), reverse=True)
                    if len(gliner_items) > 5:
                        gliner_items.sort(key=lambda x: len(x[1]), reverse=True)

                    # Remplacement sécurisé
                    for label, clean_item in gliner_items:
                        escaped_item = re.escape(clean_item)
                        prefix = r"\b" if clean_item[0].isalnum() else ""
                        suffix = r"\b" if clean_item[-1].isalnum() else ""

                        # (?<!\[) and (?!\]) prevent replacing inside existing [TAG] brackets
                        pattern = rf"(?<!\[){prefix}{escaped_item}{suffix}(?!\])"

                        new_text, count = re.subn(
                            pattern,
                            f"[{label.upper()}]",
                            redacted_text,
                            flags=re.IGNORECASE,
                        )

                        if count > 0:
                            redacted_text = new_text
                            found_entities.append(
                                {"label": label.upper(), "text": clean_item}
                            )
                else:
                    logger.warning(
                        "Fastino: API error %d: %s",
                        response.status_code,
                        response.text[:200],
                    )

            except httpx.TimeoutException:
                logger.warning("Fastino: API timeout — returning regex-only redaction")
            except Exception as exc:
                logger.error("Fastino: API call failed — %s", exc)

        # ==================================================
        # Layer 3: Restore regex mask tokens
        # ==================================================
        for token, tag in mask_map.items():
            redacted_text = redacted_text.replace(token, tag)

        if found_entities:
            logger.info(
                'Fastino: Redacted %d entit(ies) — contexts=%s — "%s"',
                len(found_entities),
                detected_contexts,
                redacted_text[:80],
            )

        return redacted_text, found_entities, detected_contexts

    def get_compliance_report(self, entities: list[dict], context: str = "") -> str:
        ctx_label = context if context else "GENERAL"
        return f"Audit {ctx_label}: {len(entities)} sensitive entit(ies) protected."
