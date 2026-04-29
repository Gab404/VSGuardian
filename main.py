"""
Secure Voice Guardian — Backend FastAPI
========================================
Audio inputs:
  1. Browser mic  → /browser-audio  (PCM 24kHz, base64)
  2. Twilio call  → /twilio-stream  (mulaw 8kHz → converted to PCM 24kHz)

Pipeline: audio → ai|coustics enhance → Gradium STT → Fastino PII → Dashboard
"""

import asyncio
import base64
import json
import logging
import time
from typing import Set

import websockets
import numpy as np  # Remplace audioop pour le traitement audio
from dotenv import load_dotenv

# Load .env BEFORE importing audio_enhancer (it reads env vars at import time)
load_dotenv()

from audio_enhancer import enhance_pcm_chunk_async, is_available as enhancer_available  # noqa: E402
from fastino_engine import FastinoPrivacyShield  # noqa: E402
from vault_manager import ZeroTrustVault  # noqa: E402
from llm_engine import get_answer, is_available as llm_available  # noqa: E402
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response

from tts_engine import text_to_speech

# ---------------------------------------------------------------------------
# Config (loaded from config.py - SECURITY FIX: no duplicate env loading)
# ---------------------------------------------------------------------------
from config import GRADIUM_API_KEY, GRADIUM_WS_URL, GRADIUM_MODEL_ID

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("guardian")

# ---------------------------------------------------------------------------
# App & shared state
# ---------------------------------------------------------------------------
app = FastAPI(title="Secure Voice Guardian")

logger.info("ai|coustics enhancer: %s", "ACTIVE" if enhancer_available() else "DISABLED (pass-through)")

# PII redaction engine
privacy_shield = FastinoPrivacyShield()
logger.info("Fastino privacy shield: %s", "ACTIVE" if privacy_shield.is_available() else "DISABLED (no API key)")

# Zero Trust Vault — stores PII entities for rehydration
vault = ZeroTrustVault()
logger.info("Zero Trust Vault: ACTIVE")

logger.info("LLM Engine: %s", "ACTIVE" if llm_available() else "DISABLED (no GROQ_API_KEY)")

ui_clients: Set[WebSocket] = set()


# ===================================================================
# Serve the dashboard
# ===================================================================
@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    """Serve the HTML/JS dashboard."""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return (
            "<h1>Error: index.html not found.</h1>"
            "<p>Make sure the file exists at the project root.</p>"
        )


# ===================================================================
# Broadcast to all connected dashboard clients
# ===================================================================
async def broadcast_to_ui(data: dict) -> None:
    """Send a JSON event to every connected UI client. Prune dead ones."""
    if not ui_clients:
        return
    dead: list[WebSocket] = []
    message = json.dumps(data, ensure_ascii=False)
    for ws in list(ui_clients):
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ui_clients.discard(ws)


# ===================================================================
# Gradium STT response parser
# ===================================================================
def parse_gradium_response(raw: str) -> dict | None:
    """Parse a Gradium STT WebSocket message."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Gradium -> non-JSON response ignored")
        return None

    msg_type = data.get("type", "")

    if msg_type == "text":
        text = data.get("text", "").strip()
        if not text:
            return None
        return {"text": text, "is_final": False}

    if msg_type in ("end_text", "end_of_stream"):
        return {"text": "", "is_final": True, "end": True}

    if msg_type not in ("ready",):
        logger.debug("Gradium -> unknown type: %s", msg_type)

    return None


# ===================================================================
# Shared pipeline helpers
# ===================================================================
async def connect_gradium():
    """Open and set up a Gradium STT WebSocket connection."""
    extra_headers = {}
    if GRADIUM_API_KEY:
        extra_headers["x-api-key"] = GRADIUM_API_KEY

    logger.info("Gradium -> connecting to %s ...", GRADIUM_WS_URL)

    gradium_ws = await websockets.connect(
        GRADIUM_WS_URL,
        additional_headers=extra_headers,
        ping_interval=20,
        ping_timeout=10,
        close_timeout=5,
    )

    logger.info("Gradium -> connected")

    setup_msg = {
        "type": "setup",
        "model_name": "default",
        "input_format": "pcm",
        "sample_rate": 24000,
    }
    await gradium_ws.send(json.dumps(setup_msg))
    logger.info("Gradium -> setup sent, waiting for ready...")

    ready_raw = await asyncio.wait_for(gradium_ws.recv(), timeout=10)
    ready_msg = json.loads(ready_raw)
    if ready_msg.get("type") != "ready":
        raise RuntimeError(f"Gradium did not confirm setup: {ready_raw}")

    logger.info("Gradium -> ready, streaming audio...")
    return gradium_ws


async def flush_gradium(gradium_ws) -> None:
    """Send 1s of silence then end_of_stream."""
    try:
        silence_bytes = b"\x00" * 48000
        silence_b64 = base64.b64encode(silence_bytes).decode("ascii")
        await gradium_ws.send(json.dumps({
            "type": "audio",
            "audio": silence_b64,
        }))
        await asyncio.sleep(1.0)
        await gradium_ws.send(json.dumps({"type": "end_of_stream"}))
    except Exception:
        pass


async def send_chunk_to_gradium(gradium_ws, audio_b64: str, enhance_ms: float = 0):
    await gradium_ws.send(json.dumps({
        "type": "audio",
        "audio": audio_b64,
    }))
    if enhance_ms > 0:
        await broadcast_to_ui({
            "type": "metrics",
            "enhance_ms": round(enhance_ms, 1),
        })


async def enhance_and_send(gradium_ws, pcm_bytes: bytes) -> asyncio.Task:
    async def _enhance_timed(pcm: bytes) -> tuple[bytes, float]:
        t0 = time.perf_counter()
        result = await enhance_pcm_chunk_async(pcm, input_sr=24000)
        return result, (time.perf_counter() - t0) * 1000

    return asyncio.create_task(_enhance_timed(pcm_bytes))


async def drain_pending(gradium_ws, pending: asyncio.Task | None) -> None:
    if pending and not pending.done():
        try:
            enhanced, ms = await pending
            await send_chunk_to_gradium(
                gradium_ws,
                base64.b64encode(enhanced).decode("ascii"), ms,
            )
        except Exception as exc:
            logger.error("Pipeline drain error: %s", exc)


async def listen_and_broadcast(gradium_ws, session_data: dict) -> None:
    """
    Listens to Gradium STT.
    - Accumule le texte brut en mémoire.
    - Détecte un blanc (0.5s) pour déclencher Fastino UNE SEULE FOIS, puis le LLM.
    """
    accumulated = ""
    session_data["raw_parts"] = []  # Nouveau : pour stocker les phrases brutes
    
    # --- TIMEOUT BAISSÉ À 0.5 SECONDE ---
    SILENCE_TIMEOUT = 2
    last_speech_time = time.time()
    has_spoken = False

    try:
        while True:
            try:
                # On vérifie le flux réseau toutes les 0.1s pour que 
                # la détection de silence à 0.5s soit très précise.
                message = await asyncio.wait_for(gradium_ws.recv(), timeout=0.1)
                
                parsed = parse_gradium_response(message)
                if not parsed:
                    continue

                # =========================================================
                # END OF SENTENCE (Géré par Gradium) -> PLUS DE FASTINO ICI
                # =========================================================
                if parsed.get("end"):
                    if accumulated:
                        # On met juste le texte de côté, on ne traite rien.
                        session_data["raw_parts"].append(accumulated)
                        accumulated = ""
                        last_speech_time = time.time()
                        has_spoken = True

                # =========================================================
                # DURING SPEECH (Partials)
                # =========================================================
                else:
                    accumulated = parsed["text"]

                    # CORRECTION TEMPS RÉEL : On recolle la phrase complète en direct 
                    # pour éviter l'effet de "saut" quand l'utilisateur parle longuement.
                    display_text = " ".join(session_data["raw_parts"])
                    if accumulated:
                        display_text = (display_text + " " + accumulated).strip()

                    await broadcast_to_ui({
                        "type": "transcription",
                        "text": display_text,  # On envoie la phrase complète en cours !
                        "is_final": False,
                    })
                    
                    last_speech_time = time.time()
                    has_spoken = True

            except asyncio.TimeoutError:
                # =========================================================
                # DÉTECTION DE BLANC (0.5s) -> FASTINO SUR LE BLOC COMPLET
                # =========================================================
                if has_spoken and (time.time() - last_speech_time) > SILENCE_TIMEOUT:
                    
                    # 1. On regroupe tout le texte brut (phrases finies + partiel en cours)
                    full_raw_text = " ".join(session_data["raw_parts"])
                    if accumulated:
                        full_raw_text = full_raw_text + " " + accumulated if full_raw_text else accumulated
                    
                    full_raw_text = full_raw_text.strip()

                    if full_raw_text:
                        # 2. Exécution de Fastino (UNE SEULE FOIS)
                        t0 = time.perf_counter()
                        if privacy_shield.is_available():
                            redacted, entities, contexts = await privacy_shield.analyze_and_redact(full_raw_text)
                        else:
                            redacted, entities, contexts = full_raw_text, [], []
                        
                        redact_ms = (time.perf_counter() - t0) * 1000

                        vault_id = ""
                        if entities:
                            ctx_label = ",".join(contexts) if contexts else "GENERAL"
                            vault_id = await asyncio.to_thread(vault.secure_store, ctx_label, entities)
                        
                        # 3. Envoi au Dashboard (Double affichage Avant / Après)
                        await broadcast_to_ui({
                            "type": "transcription",
                            "raw_text": full_raw_text, 
                            "text": redacted,
                            "contexts": contexts,
                            "redact_ms": round(redact_ms, 1),
                            "vault_id": vault_id,
                            "is_final": True,
                        })

                        # 4. Lancement du LLM avec le texte protégé
                        if llm_available() and redacted.strip():
                            asyncio.create_task(fire_llm_answer(redacted, list(entities)))
                    
                    # 5. Réinitialisation pour la prochaine question
                    session_data["raw_parts"].clear()
                    accumulated = ""
                    has_spoken = False

    except websockets.exceptions.ConnectionClosed:
        logger.info("Gradium -> connection closed")
    except asyncio.CancelledError:
        logger.info("Gradium -> listener cancelled")
    except Exception as exc:
        logger.error("Gradium -> listen error: %s", exc)
    finally:
        # Flush de sécurité final à la déconnexion
        full_raw_text = " ".join(session_data.get("raw_parts", []))
        if accumulated:
            full_raw_text = full_raw_text + " " + accumulated if full_raw_text else accumulated
        
        full_raw_text = full_raw_text.strip()
        if full_raw_text:
            if privacy_shield.is_available():
                redacted, entities, _ = await privacy_shield.analyze_and_redact(full_raw_text)
            else:
                redacted, entities = full_raw_text, []
            
            await broadcast_to_ui({
                "type": "transcription",
                "text": redacted,
                "is_final": True,
            })


async def fire_llm_answer(redacted_transcript: str, entities: list[dict]) -> None:
    """Send full redacted transcript to LLM, rehydrate with vault, broadcast and send TTS audio to UI."""
    try:
        t0 = time.perf_counter()
        answer_redacted = await get_answer(redacted_transcript)
        llm_ms = (time.perf_counter() - t0) * 1000
        if not answer_redacted:
            return

        answer_rehydrated = vault.rehydrate_with_entities(
            answer_redacted, entities
        )

        logger.info(
            "LLM [answer] redacted=\"%s\" -> rehydrated=\"%s\" (%.1fms)",
            answer_redacted[:80], answer_rehydrated[:80], llm_ms,
        )

        await broadcast_to_ui({
            "type": "llm_answer",
            "redacted": answer_redacted,
            "rehydrated": answer_rehydrated,
            "llm_ms": round(llm_ms, 1),
        })

        # =========================================================
        # TTS : GÉNÉRATION ET ENVOI AU NAVIGATEUR
        # =========================================================
        logger.info("TTS -> Génération audio Gradium en cours...")
        
        pcm_bytes = await text_to_speech(answer_rehydrated)

        if pcm_bytes:
            logger.info("TTS -> Envoi de l'audio PCM au Dashboard web.")
            
            # On encode le binaire PCM en base64 pour le passer dans le JSON
            audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
            
            await broadcast_to_ui({
                "type": "play_audio",
                "audio_pcm_b64": audio_b64
            })
        else:
            logger.error("TTS -> Échec de la génération audio.")

    except Exception as exc:
        logger.error("LLM answer error: %s", exc)


async def run_pipeline(gradium_ws, forward_task_coro) -> None:
    """Run audio forwarding + STT listener. Fire LLM answer once when done."""
    # Shared mutable dict — survives task cancellation
    session_data = {"parts": [], "entities": []}

    forward_task = asyncio.create_task(forward_task_coro)
    listener_task = asyncio.create_task(listen_and_broadcast(gradium_ws, session_data))

    done, pending = await asyncio.wait(
        [forward_task, listener_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Fire LLM answer once with the full session transcript
    full_redacted = " ".join(session_data["parts"])
    all_entities = session_data["entities"]
    if llm_available() and full_redacted.strip():
        await fire_llm_answer(full_redacted, all_entities)


# ===================================================================
# WebSocket — Browser audio → Gradium STT → Dashboard
# ===================================================================
@app.websocket("/browser-audio")
async def browser_audio(ws: WebSocket):
    await ws.accept()
    logger.info("Browser -> mic connection established")

    vault.clear()
    gradium_ws = None

    try:
        gradium_ws = await connect_gradium()

        await broadcast_to_ui({
            "type": "status",
            "text": "STT connected — listening...",
            "status": "connected",
        })

        async def forward_audio_to_gradium():
            pending_enhance: asyncio.Task | None = None

            try:
                while True:
                    raw = await ws.receive_text()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if data.get("type") == "stop":
                        logger.info("Browser -> stop signal received, flushing...")
                        await drain_pending(gradium_ws, pending_enhance)
                        break

                    audio_b64 = data.get("audio_b64")
                    if not audio_b64:
                        continue

                    if enhancer_available():
                        if pending_enhance and pending_enhance.done():
                            try:
                                enhanced, ms = pending_enhance.result()
                                await send_chunk_to_gradium(
                                    gradium_ws,
                                    base64.b64encode(enhanced).decode("ascii"), ms,
                                )
                            except Exception as exc:
                                logger.error("Pipeline send error: %s", exc)
                            pending_enhance = None

                        if pending_enhance and not pending_enhance.done():
                            await drain_pending(gradium_ws, pending_enhance)

                        pcm_bytes = base64.b64decode(audio_b64)
                        pending_enhance = await enhance_and_send(gradium_ws, pcm_bytes)
                    else:
                        await send_chunk_to_gradium(gradium_ws, audio_b64)

            except WebSocketDisconnect:
                logger.info("Browser -> mic disconnected")
            except Exception as exc:
                logger.error("Browser -> forward error: %s", exc)
            finally:
                await drain_pending(gradium_ws, pending_enhance)
                await flush_gradium(gradium_ws)

        await run_pipeline(gradium_ws, forward_audio_to_gradium())

    except websockets.exceptions.InvalidHandshake as exc:
        logger.error("Gradium -> handshake failed: %s", exc)
        await broadcast_to_ui({"type": "error", "text": f"Gradium connection refused: {exc}"})
    except websockets.exceptions.WebSocketException as exc:
        logger.error("Gradium -> WebSocket error: %s", exc)
        await broadcast_to_ui({"type": "error", "text": f"Gradium error: {exc}"})
    except WebSocketDisconnect:
        logger.info("Browser -> disconnected before Gradium relay")
    except Exception as exc:
        logger.error("browser-audio -> unexpected error: %s", exc)
        await broadcast_to_ui({"type": "error", "text": f"Server error: {exc}"})
    finally:
        if gradium_ws:
            await gradium_ws.close()
        await broadcast_to_ui({
            "type": "status",
            "text": "Mic disconnected",
            "status": "disconnected",
        })
        logger.info("browser-audio -> session ended")


# ===================================================================
# WebSocket — UI Dashboard
# ===================================================================
@app.websocket("/ui-stream")
async def ui_stream(ws: WebSocket):
    await ws.accept()
    ui_clients.add(ws)
    logger.info("Dashboard -> client connected (%d total)", len(ui_clients))

    try:
        await ws.send_text(json.dumps({
            "type": "init",
            "message": "Connected to Guardian server",
        }, ensure_ascii=False))
    except Exception:
        ui_clients.discard(ws)
        return

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ui_clients.discard(ws)
        logger.info("Dashboard -> client disconnected (%d remaining)", len(ui_clients))


# ===================================================================
# Rehydration endpoint
# ===================================================================
@app.post("/rehydrate/{session_id}")
async def rehydrate(session_id: str, body: dict):
    censored_text = body.get("text", "")
    if not censored_text:
        return JSONResponse(status_code=400, content={"error": "Missing 'text' field"})

    result = vault.rehydrate(censored_text, session_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": f"Session {session_id} not found"})

    return {"text": result, "session_id": session_id}


@app.get("/vault/{session_id}")
async def get_vault_session(session_id: str):
    session = vault.get_session(session_id)
    if session is None:
        return JSONResponse(status_code=404, content={"error": f"Session {session_id} not found"})
    return {"session_id": session_id, **session}


# ===================================================================
# Health check
# ===================================================================
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ui_clients": len(ui_clients),
        "gradium_configured": bool(GRADIUM_API_KEY),
        "ai_coustics_active": enhancer_available(),
        "fastino_pii_active": privacy_shield.is_available(),
    }