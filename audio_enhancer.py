"""
ai|coustics Audio Enhancer — aic-sdk (optimized)
==================================================
Uses the standalone aic-sdk Processor to run the QUAIL Voice Focus model
locally for noise cancellation.

Optimization: sync Processor wrapped in a single asyncio.to_thread() call.
This processes all ~33 frames per chunk in one thread dispatch instead of
33 separate async dispatches — eliminates Python/event-loop overhead.

Model: quail-vf-2.1-l-16khz (16kHz, optimal 240 frames = 15ms window)
Algorithmic delay: 30ms (inherent to model, unavoidable).
Falls back to pass-through gracefully if model or license unavailable.
"""

import asyncio
import logging
import os

import numpy as np

logger = logging.getLogger("guardian.enhancer")

# ---------------------------------------------------------------------------
# Configuration (SECURITY FIX: Added validation)
# ---------------------------------------------------------------------------
AIC_SDK_LICENSE = os.getenv("AIC_SDK_LICENSE", "").strip()
MODEL_DIR = os.getenv("AIC_MODEL_DIR", "./models").strip()
MODEL_NAME = "quail-vf-2.1-l-16khz"

# Validate configuration
if not os.path.isdir(MODEL_DIR):
    logger.warning("ai|coustics: MODEL_DIR does not exist — %s", MODEL_DIR)

# ---------------------------------------------------------------------------
# Load aic-sdk Processor (sync — we wrap it ourselves)
# ---------------------------------------------------------------------------
_processor = None  # aic.Processor (sync)
_config = None
_enhancer_available = False
MODEL_SAMPLE_RATE = 16000
FRAME_SAMPLES = 240  # updated after config loads

try:
    import aic_sdk as aic

    if not AIC_SDK_LICENSE:
        logger.warning("ai|coustics: AIC_SDK_LICENSE not set — enhancer disabled")
    else:
        model_path = aic.Model.download(MODEL_NAME, MODEL_DIR)
        logger.info("ai|coustics: Model loaded from %s", model_path)

        model = aic.Model.from_file(model_path)

        # Optimal config: 16kHz, mono, 240 frames (15ms)
        # Using optimal frame size avoids adapter delay (see docs)
        _config = aic.ProcessorConfig.optimal(model, num_channels=1)
        MODEL_SAMPLE_RATE = _config.sample_rate
        FRAME_SAMPLES = _config.num_frames

        # Sync processor — we wrap it in asyncio.to_thread ourselves
        # This is faster than ProcessorAsync because we batch all frames
        # in a single thread dispatch instead of 33 separate ones.
        _processor = aic.Processor(model, AIC_SDK_LICENSE, _config)
        _enhancer_available = True

        logger.info(
            "ai|coustics: Processor ready (model=%s, sr=%d, frames=%d, delay=30ms)",
            MODEL_NAME, MODEL_SAMPLE_RATE, FRAME_SAMPLES,
        )

except Exception as exc:
    logger.warning("ai|coustics: Failed to load enhancer — falling back to pass-through: %s", exc)


# ---------------------------------------------------------------------------
# Resampling helpers
# ---------------------------------------------------------------------------
def _resample(pcm: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    """Resample a 1D float32 array using linear interpolation."""
    if from_sr == to_sr:
        return pcm
    duration = len(pcm) / from_sr
    n_out = int(duration * to_sr)
    x_old = np.linspace(0, duration, len(pcm), endpoint=False)
    x_new = np.linspace(0, duration, n_out, endpoint=False)
    return np.interp(x_new, x_old, pcm).astype(np.float32)


# ---------------------------------------------------------------------------
# Core: sync processing of a full chunk (runs in thread)
# ---------------------------------------------------------------------------
def _enhance_chunk_sync(pcm_bytes: bytes, input_sr: int) -> bytes:
    """Process an entire PCM chunk through ai|coustics. BLOCKING.

    This runs the full resample → frame loop → resample pipeline in one go.
    Designed to be called from asyncio.to_thread() for a single dispatch.
    """
    # int16 PCM -> float32 [-1, 1]
    pcm_int16 = np.frombuffer(pcm_bytes, dtype=np.int16).copy()
    samples = pcm_int16.astype(np.float32) / 32768.0

    # Resample to model rate (16kHz)
    if input_sr != MODEL_SAMPLE_RATE:
        samples = _resample(samples, input_sr, MODEL_SAMPLE_RATE)

    # Process all frames in a tight loop (no Python async overhead)
    n_chunks = len(samples) // FRAME_SAMPLES
    output_parts = []

    for i in range(n_chunks):
        start = i * FRAME_SAMPLES
        frame = samples[start : start + FRAME_SAMPLES].reshape(1, -1).copy()
        enhanced = _processor.process(frame)
        output_parts.append(enhanced.flatten())

    # Handle remainder (pad to frame size, process, trim)
    remainder = len(samples) - n_chunks * FRAME_SAMPLES
    if remainder > 0:
        start = n_chunks * FRAME_SAMPLES
        padded = np.zeros(FRAME_SAMPLES, dtype=np.float32)
        padded[:remainder] = samples[start : start + remainder]
        enhanced = _processor.process(padded.reshape(1, -1).copy())
        output_parts.append(enhanced.flatten()[:remainder])

    if output_parts:
        samples = np.concatenate(output_parts)

    # Resample back to original rate
    if input_sr != MODEL_SAMPLE_RATE:
        samples = _resample(samples, MODEL_SAMPLE_RATE, input_sr)

    # float32 -> int16 PCM
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def enhance_pcm_chunk_async(pcm_bytes: bytes, input_sr: int = 24000) -> bytes:
    """Enhance audio without blocking the event loop.

    Dispatches the entire chunk to a thread in ONE call (not per-frame).
    ~33 frames processed in native speed inside the thread.
    """
    if not _enhancer_available:
        return pcm_bytes
    try:
        return await asyncio.to_thread(_enhance_chunk_sync, pcm_bytes, input_sr)
    except Exception as exc:
        logger.error("ai|coustics: Enhancement failed — passing through: %s", exc)
        return pcm_bytes


def enhance_pcm_chunk(pcm_bytes: bytes, input_sr: int = 24000) -> bytes:
    """Enhance audio synchronously (blocks). Use enhance_pcm_chunk_async instead."""
    if not _enhancer_available:
        return pcm_bytes
    try:
        return _enhance_chunk_sync(pcm_bytes, input_sr)
    except Exception as exc:
        logger.error("ai|coustics: Enhancement failed — passing through: %s", exc)
        return pcm_bytes


def is_available() -> bool:
    """Check if the ai|coustics enhancer is loaded and ready."""
    return _enhancer_available
