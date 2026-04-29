# tts_engine.py
import json
import ssl
import asyncio
import websockets
import config
import base64

GRADIUM_API_KEY = config.GRADIUM_API_KEY
GRADIUM_WS_URL = config.GRADIUM_TTS_URL

# NOUVEAU : Verrou global pour empêcher les générations concurrentes
tts_lock = asyncio.Lock()

async def text_to_speech(text: str) -> bytes | None:
    """
    Se connecte à Gradium en mode ASYNCHRONE, envoie le texte, 
    et retourne le flux PCM brut (bytes).
    Vérifie qu'aucune autre génération n'est en cours.
    """
    # Si le verrou est déjà pris, on annule silencieusement cette nouvelle requête
    if tts_lock.locked():
        print("[LOG] TTS refusé : L'agent est déjà en train de parler.")
        return None

    # On prend le verrou (le feu passe au rouge pour les autres)
    async with tts_lock:
        print("[LOG] Connexion WebSocket TTS Gradium (Async)...")

        # Format des headers pour la librairie 'websockets'
        headers = {"x-api-key": GRADIUM_API_KEY}
        audio_buffer = bytearray()

        # Configuration SSL with certificate verification enabled (SECURITY FIX)
        ssl_context = ssl.create_default_context()
        # Properly verify hostname and certificates
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED

        try:
            # Ouverture de la connexion asynchrone
            async with websockets.connect(
                GRADIUM_WS_URL, 
                additional_headers=headers, 
                ssl=ssl_context,
                ping_interval=None,
                ping_timeout=None
            ) as ws:

                # ==========================================
                # ÉTAPE 1 : Setup
                # ==========================================
                setup_payload = {
                    "type": "setup",
                    "voice_id": "YTpq7expH9539ERJ",
                    "model_name": "default",
                    "output_format": "pcm",  # PCM brut (48kHz, 16-bit, Mono)
                }
                await ws.send(json.dumps(setup_payload))

                while True:
                    response = await ws.recv()
                    msg = json.loads(response)
                    if msg.get("type") == "ready":
                        break
                    elif msg.get("type") == "error":
                        print(f"[ERROR] Erreur de Setup TTS: {msg}")
                        return None

                # ==========================================
                # ÉTAPE 2 : Envoi du texte
                # ==========================================
                await ws.send(json.dumps({"type": "text", "text": text}))
                await ws.send(json.dumps({"type": "end_of_stream"}))

                # ==========================================
                # ÉTAPE 3 : Réception du flux audio
                # ==========================================
                while True:
                    response = await ws.recv()
                    msg = json.loads(response)

                    if msg.get("type") == "audio":
                        audio_buffer.extend(base64.b64decode(msg["audio"]))
                    elif msg.get("type") == "end_of_stream":
                        break
                    elif msg.get("type") == "error":
                        print(f"[ERROR] Serveur Gradium TTS : {msg.get('message')}")
                        break

            # Retourne simplement les bytes PCM une fois la connexion fermée proprement
            if len(audio_buffer) > 0:
                return bytes(audio_buffer)
            else:
                return None

        except Exception as e:
            print(f"[ERROR] Exception fatale TTS : {e}")
            return None
        # À la fin du bloc "async with tts_lock", le verrou est automatiquement libéré.