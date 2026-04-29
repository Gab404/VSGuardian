"""
Secure Voice Guardian — Streamlit Dashboard
=============================================
Tableau de bord temps réel connecté au backend FastAPI via WebSocket.
Affiche les transcriptions censurées et le compteur de risques bloqués.

Lancer avec :  streamlit run dashboard.py
"""

import json
import re
import threading
import time

import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Secure Voice Guardian",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Constants (SECURITY FIX: Use secure WebSocket - wss instead of ws)
# ---------------------------------------------------------------------------
# In production, use 'wss://' for encrypted WebSocket connections
# Development can use 'ws://' for localhost only
BACKEND_WS_URL = "wss://localhost:8000/ui-stream"  # Use 'wss://' in production
PII_TAG_PATTERN = re.compile(r"(\[[A-ZÀ-Ü_É]+\])")

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "risk_count" not in st.session_state:
    st.session_state.risk_count = 0
if "ws_connected" not in st.session_state:
    st.session_state.ws_connected = False
if "ws_thread_started" not in st.session_state:
    st.session_state.ws_thread_started = False


# ---------------------------------------------------------------------------
# Background WebSocket listener (runs in a daemon thread)
# ---------------------------------------------------------------------------
def _ws_listener():
    """Connect to the FastAPI /ui-stream WebSocket and push messages
    into st.session_state (thread-safe via GIL for simple appends)."""
    import websocket  # websocket-client library

    while True:
        try:
            ws = websocket.WebSocket()
            ws.connect(BACKEND_WS_URL, timeout=5)
            st.session_state.ws_connected = True

            while True:
                raw = ws.recv()
                if not raw:
                    break
                data = json.loads(raw)

                if data.get("type") == "init":
                    st.session_state.risk_count = data.get("risk_count", 0)

                elif data.get("type") == "transcription":
                    st.session_state.risk_count = data.get("risk_count", 0)
                    st.session_state.messages.append({
                        "text": data.get("text", ""),
                        "detections": data.get("detections", 0),
                        "timestamp": time.strftime("%H:%M:%S"),
                    })
                    # Keep only last 200 messages to avoid memory bloat
                    if len(st.session_state.messages) > 200:
                        st.session_state.messages = st.session_state.messages[-200:]

        except Exception:
            st.session_state.ws_connected = False
            time.sleep(2)  # Retry backoff


# Start the background thread once
if not st.session_state.ws_thread_started:
    t = threading.Thread(target=_ws_listener, daemon=True)
    t.start()
    st.session_state.ws_thread_started = True


# ---------------------------------------------------------------------------
# Auto-refresh every 1.5 seconds
# ---------------------------------------------------------------------------
st_autorefresh(interval=1500, limit=None, key="auto_refresh")


# ---------------------------------------------------------------------------
# Custom CSS for dark, modern look
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Force dark background everywhere */
    .stApp {
        background-color: #0f1117;
    }
    .block-container {
        padding-top: 2rem;
    }
    /* Header styling */
    .guardian-header {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 1.5rem 2rem;
        margin-bottom: 1.5rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .guardian-title {
        font-size: 1.8rem;
        font-weight: 700;
        color: #f1f5f9;
        margin: 0;
    }
    .guardian-subtitle {
        font-size: 0.95rem;
        color: #94a3b8;
        margin: 0;
    }
    /* Risk counter card */
    .risk-card {
        background: linear-gradient(135deg, #7f1d1d 0%, #991b1b 100%);
        border: 1px solid #dc2626;
        border-radius: 12px;
        padding: 2rem;
        text-align: center;
    }
    .risk-number {
        font-size: 4rem;
        font-weight: 800;
        color: #fecaca;
        line-height: 1;
    }
    .risk-label {
        font-size: 1rem;
        color: #fca5a5;
        margin-top: 0.5rem;
    }
    /* Status card */
    .status-card {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 2rem;
        text-align: center;
    }
    .status-dot-green {
        display: inline-block;
        width: 12px; height: 12px;
        background: #22c55e;
        border-radius: 50%;
        margin-right: 8px;
        box-shadow: 0 0 8px #22c55e;
    }
    .status-dot-red {
        display: inline-block;
        width: 12px; height: 12px;
        background: #ef4444;
        border-radius: 50%;
        margin-right: 8px;
        box-shadow: 0 0 8px #ef4444;
    }
    .status-text {
        font-size: 1.2rem;
        color: #e2e8f0;
    }
    /* Pipeline card */
    .pipeline-card {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 1.5rem 2rem;
    }
    .pipeline-step {
        display: inline-block;
        background: #334155;
        color: #cbd5e1;
        padding: 0.3rem 0.8rem;
        border-radius: 6px;
        margin: 0.2rem;
        font-size: 0.85rem;
        font-family: monospace;
    }
    .pipeline-arrow {
        color: #64748b;
        margin: 0 0.3rem;
    }
    /* Transcript area */
    .transcript-box {
        background: #020617;
        border: 1px solid #1e293b;
        border-radius: 10px;
        padding: 1.5rem;
        max-height: 450px;
        overflow-y: auto;
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
        font-size: 0.9rem;
    }
    .transcript-line {
        padding: 0.5rem 0.8rem;
        margin-bottom: 0.4rem;
        border-left: 3px solid #334155;
        color: #cbd5e1;
        border-radius: 0 6px 6px 0;
        background: #0f172a;
    }
    .transcript-line.has-pii {
        border-left-color: #ef4444;
        background: #1a0a0a;
    }
    .pii-tag {
        background: #dc2626;
        color: #fff;
        padding: 0.1rem 0.5rem;
        border-radius: 4px;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .ts {
        color: #475569;
        font-size: 0.8rem;
        margin-right: 0.5rem;
    }
    /* Section title */
    .section-title {
        color: #e2e8f0;
        font-size: 1.1rem;
        font-weight: 600;
        margin-bottom: 0.8rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    /* Hide default streamlit elements */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown("""
<div class="guardian-header">
    <div>
        <p class="guardian-title">&#128737; Secure Voice Guardian</p>
        <p class="guardian-subtitle">Live Privacy Monitor &mdash; Call Center Middleware</p>
    </div>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Top metrics row
# ---------------------------------------------------------------------------
col1, col2, col3 = st.columns([1, 1, 2])

with col1:
    count = st.session_state.risk_count
    st.markdown(f"""
    <div class="risk-card">
        <div class="risk-number">{count}</div>
        <div class="risk-label">Risques Bloqués</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    connected = st.session_state.ws_connected
    dot_class = "status-dot-green" if connected else "status-dot-red"
    status_label = "Connecté" if connected else "Déconnecté"
    msg_count = len(st.session_state.messages)
    st.markdown(f"""
    <div class="status-card">
        <div class="status-text">
            <span class="{dot_class}"></span> {status_label}
        </div>
        <div style="color:#64748b; margin-top:1rem; font-size:0.9rem;">
            {msg_count} transcription{"s" if msg_count != 1 else ""} reçue{"s" if msg_count != 1 else ""}
        </div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown("""
    <div class="pipeline-card">
        <div class="section-title">Pipeline de traitement</div>
        <span class="pipeline-step">&#127911; Twilio Audio</span>
        <span class="pipeline-arrow">&#10132;</span>
        <span class="pipeline-step">&#128264; ai|coustics</span>
        <span class="pipeline-arrow">&#10132;</span>
        <span class="pipeline-step">&#128221; Gradium STT</span>
        <span class="pipeline-arrow">&#10132;</span>
        <span class="pipeline-step">&#128737; Fastino PII</span>
        <span class="pipeline-arrow">&#10132;</span>
        <span class="pipeline-step">&#129302; Telli LLM</span>
        <span class="pipeline-arrow">&#10132;</span>
        <span class="pipeline-step">&#128266; Gradium TTS</span>
    </div>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Live Transcription feed
# ---------------------------------------------------------------------------
st.markdown("""
<div class="section-title" style="margin-top:1.5rem;">
    &#128308; Live Transcription
</div>
""", unsafe_allow_html=True)


def _highlight_pii(text: str) -> str:
    """Wrap PII tags like [USER_NAME] in red badge HTML."""
    def _replacer(match):
        tag = match.group(1)
        return f'<span class="pii-tag">{tag}</span>'
    return PII_TAG_PATTERN.sub(_replacer, text)


messages = st.session_state.messages
if not messages:
    st.markdown("""
    <div class="transcript-box">
        <div style="color:#475569; text-align:center; padding:3rem;">
            En attente de transcriptions&hellip;<br>
            <span style="font-size:0.8rem;">
                Déclenchez un appel Twilio ou utilisez
                <code>POST /demo/trigger</code> pour tester.
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)
else:
    lines_html = []
    # Show newest first
    for msg in reversed(messages[-50:]):
        has_pii = msg.get("detections", 0) > 0
        css_class = "transcript-line has-pii" if has_pii else "transcript-line"
        highlighted = _highlight_pii(msg["text"])
        ts = msg.get("timestamp", "")
        lines_html.append(
            f'<div class="{css_class}">'
            f'<span class="ts">{ts}</span> {highlighted}'
            f'</div>'
        )
    st.markdown(
        f'<div class="transcript-box">{"".join(lines_html)}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Sidebar — Quick help
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Guide rapide")
    st.markdown("""
**Lancer le backend :**
```bash
uvicorn main:app --reload --port 8000
```

**Tester sans Twilio :**
```bash
curl -X POST https://localhost:8000/demo/trigger
```

**Exposer pour Twilio :**
```bash
ngrok http 8000
```
Puis configurer le webhook Twilio :
`wss://<id>.ngrok.io/twilio-stream`
    """)
    st.markdown("---")
    st.markdown(
        '<p style="color:#475569;font-size:0.8rem;">Secure Voice Guardian v0.1 — POC</p>',
        unsafe_allow_html=True,
    )
