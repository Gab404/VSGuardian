# Voice Security Guardian Protocol

Real-time speech-to-text privacy middleware for call centers. Captures audio from the browser microphone, cleans it with ai|coustics noise cancellation, streams it to Gradium STT for live transcription, and displays results on a real-time dashboard.

Youtube presentation [here](https://www.youtube.com/watch?v=1hgUG4R3S90).

> **Status:** Proof of Concept — Built at Berlin Hackathon 2026

---

## Architecture

```
Phone / Browser (mic)
    │
    │  AudioWorklet (PCM 24kHz, 500ms chunks, base64)
    │
    ▼
FastAPI Backend (main.py)
    │
    ├── ai|coustics (QUAIL VF-L, 16kHz local denoise)
    │         │
    │         ▼
    ├── /browser-audio  WS ──► Gradium STT (wss://api.gradium.ai)
    │                               │
    │                               ▼
    │                         Transcription JSON
    │                               │
    │                               ▼
    │                    Fastino Privacy Shield
    │                    ┌─────────────────────┐
    │                    │ 1. Regex layer       │
    │                    │    (email, phone,    │
    │                    │    credit card)      │
    │                    │ 2. GLiNER NER layer  │
    │                    │    (Pioneer API)     │
    │                    │ 3. Context detection │
    │                    │    (CRIME / MEDICAL  │
    │                    │    / FINANCE, ...)   │
    │                    └────────┬────────────┘
    │                             │
    │                    ┌────────▼────────────┐
    │                    │  vault.json         │
    │                    │  (PII ↔ token map,  │
    │                    │  session-scoped)     │
    │                    └────────┬────────────┘
    │                             │
    │                             ▼
    │                  Anonymized text (categories only)
    │                             │
    │                             ▼
    │                  Groq API — Llama (fast inference)
    │                  (no PII ever sent)
    │                             │
    │                             ▼
    │                  De-anonymization pass
    │                  (vault tokens → original values)
    │                             │
    └── /ui-stream WS ◄───────────┘ broadcast
            │
            ▼
     Dashboard (index.html) — live transcript terminal
```

---

## Project structure

```
VSGuardian/
├── main.py                # FastAPI backend — mic → ai|coustics → Gradium STT → Fastino → Groq → dashboard
├── audio_enhancer.py      # ai|coustics FFI wrapper (resample + 10ms frame processing)
├── fastino_engine.py      # Fastino Privacy Shield — regex + GLiNER PII redaction engine
├── llm_engine.py          # Groq API wrapper — anonymized text → Llama inference
├── vault_manager.py       # Session-scoped vault — PII token storage & de-anonymization
├── config.py              # Centralized config — entity labels, context rules, model IDs
├── dashboard.py           # Dashboard backend helpers
├── tts_engine.py          # Text-to-speech engine
├── index.html             # Dashboard — dark UI with mic button + live transcript
├── models/                # Local model files (ai|coustics Quail weights)
├── src/                   # 
│   └── agent.py           # LiveKit agent with ai|coustics (standalone alternative)
├── .env.example           # Environment variable template
├── requirements.txt       # Python dependencies
└── README.md
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Create a `.env` file (already included .env.example)

### 3. Run

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Check the startup logs if needed:

```
ai|coustics enhancer: ACTIVE        ← noise cancellation enabled
```

or:

```
ai|coustics enhancer: DISABLED (pass-through)   ← model not available, audio passes through unchanged
```

**Open http://localhost:8000 — tap the mic button, speak, see live transcription.**

### 3.5. If you want to access from phone (you need to setup ngrok beforehand)

in another terminal :
```bash
ngrok http 8000
```

Open the ngrok HTTPS URL on your phone. Tap the mic, speak — transcription appears in real time.

### 4. Verify status if needed

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "ui_clients": 1,
  "gradium_configured": true,
  "ai_coustics_active": true
}
```

---

## Endpoints

| Endpoint | Type | Purpose |
|---|---|---|
| `/` | GET | Serves the dashboard (index.html) |
| `/browser-audio` | WebSocket | Receives PCM audio, enhances via ai\|coustics, relays to Gradium STT |
| `/ui-stream` | WebSocket | Sends live transcriptions to the dashboard |
| `/health` | GET | Health check (shows ai\|coustics status, Gradium config, connected clients) |

---

## How the full pipeline works

1. Browser captures audio via `AudioContext` at 24kHz sample rate
2. `AudioWorkletNode` accumulates 500ms of PCM samples, converts Float32 → Int16, encodes base64
3. Chunks are sent to `/browser-audio` WebSocket
4. **ai|coustics** cleans the audio (resample → denoise → resample back)

---
*This is where the audio is conditioned before it touches the STT engine. ai|coustics is a Berlin-based audio intelligence SDK purpose-built for Voice AI pipelines — not for making audio sound good to a human ear, but for making it machine-readable. The three-step process in our pipeline is:
Resample in — the chunk is resampled to the expected frequency (16kHz)
Denoise — the model suppresses background noise, reverb, and artifacts while preserving the speaker's voice
Resample back — audio is resampled back to 24kHz for Gradium compatibility*

*The key advantage here is that audio is processed on-device with no data leaving our infrastructure, which is critical for a pipeline handling sensitive transcription data. ai|coustics reduces Word Error Rate by up to 30% ai-coustics, which directly improves the accuracy of everything downstream.*

---
5. Clean audio is forwarded to Gradium STT (`x-api-key` auth, `setup` → `ready` → `audio` → `end_of_stream` protocol)

---
*Gradium is our Speech-to-Text engine. The connection follows a stateful handshake protocol over WebSocket:
setup — client sends configuration (language, audio format, sample rate)
ready — server confirms it's ready to receive audio
audio — binary PCM chunks are streamed continuously
end_of_stream — client signals the end of the session, prompting Gradium to finalize its last prediction
Authentication uses a static x-api-key header, validated on each WebSocket upgrade request.*

---
6. Gradium returns incremental text fragments — backend accumulates them into a growing sentence

---
*Gradium operates in streaming mode: it doesn't wait for silence to return a result. Instead it continuously emits partial hypotheses as it processes audio — for example "bon" → "bonjour" → "bonjour je" → "bonjour je m'appelle". The backend keeps only the latest fragment per in-progress sentence, overwriting previous partials.*

---
7. Each update is broadcast to all `/ui-stream` clients (phone and computer)
8. Dashboard displays partial text (gray italic) and finalized sentences (green)
9. On stop: 1s silence padding is sent to Gradium so the last word is captured
10. Fastino Privacy Shield redacts PII before any text reaches the LLM

---

**Sensitive data never goes to server !**

*Once a sentence is finalized by Gradium, it passes through Fastino — our context-aware PII redaction engine — before being forwarded anywhere else. This is a critical privacy firewall: the thinking LLM never sees raw sensitive data.*

*Fastino operates in two complementary layers:
Regex layer (first pass) — fast, deterministic patterns catch structured PII immediately: emails, phone numbers, credit card numbers, .... Each match is replaced by a secure UUID-based mask token (e.g. __FSTNO_MSK_a3f9c12b__) to prevent the NER model from interfering with them.
GLiNER NER layer (second pass) — the sentence (with regex tokens already in place) is sent to the Pioneer API, which runs a GLiNER Named Entity Recognition model. GLiNER is a generalist zero-shot NER model that can detect entity types it was not explicitly trained on, by matching spans of text against a provided list of label names. Our pipeline sends it labels such as PERSON, LOCATION, ORG, DATE, and so on.*

*Fastino is also context-aware: before calling the API, it scans the sentence for domain-specific keywords. If it detects vocabulary related to crime, medical, or finance contexts, it automatically expands the entity label list with domain-specific types (e.g. MEDICAL_CONDITION, BANK_ACCOUNT), making the redaction adaptive to the conversation topic without any manual configuration.*

*Each detected entity is replaced inline by its category tag: "Jean Dupont lived at the 12th Washington Street" becomes "[PERSON] lived at [ADDRESS]". An anti-hallucination filter (STOP_WORDS set + heuristic rules on casing and length) prevents common words from being incorrectly flagged by the model.*

---
11. Anonymized text is sent to a fast Llama model via Groq, original values are stored in the vault
---
*In parallel with the redaction step, every original value that was masked is stored in a local vault.json, keyed by its mask token or category tag and position.
The sanitized sentence is then sent to a Llama model (llama-3.1-8b-instant) served by Groq. Groq is an inference platform built around its proprietary LPU (Language Processing Unit) chip, designed specifically to run LLM inference at very high token throughput and low latency — significantly faster than GPU-based inference for this kind of small, frequent request. We use a lightweight Llama model (not a large frontier model) because the task doesn't require deep reasoning: the model only needs to interpret the call context and generate a structured response from the anonymized text. Keeping the model small keeps the latency low and the cost negligible per call.*

*Critically, the Llama model never sees any PII — it only receives category placeholders like [PERSON], [ADDRESS], [PHONE]. It reasons purely over the semantic structure of the conversation.*

*Once the LLM produces its output, a de-anonymization pass replaces the category placeholders back with the original values recovered from the vault, reconstructing a coherent, human-readable response with full fidelity. The vault is session-scoped and never persisted beyond the conversation lifetime.*

---