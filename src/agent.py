"""
Secure Voice Guardian — LiveKit Agent with ai|coustics
=======================================================
Standalone voice agent using LiveKit with ai|coustics for:
- Real-time noise cancellation (QUAIL Voice Focus L model)
- Voice Activity Detection (VAD)

This is a SEPARATE service from the FastAPI dashboard (main.py).
It connects to a LiveKit Cloud room and processes audio there.

Setup:
    1. lk cloud auth                          # Authenticate with LiveKit Cloud
    2. uv add livekit-plugins-ai-coustics     # Add the plugin
    3. uv sync                                # Install dependencies
    4. uv run src/agent.py download-files     # Download model files locally
    5. uv run src/agent.py console            # Run in console mode (dev)
    6. uv run src/agent.py dev                # Run in dev mode with LiveKit room

Note: The ai|coustics LiveKit plugin authenticates through LiveKit Cloud,
      not through a separate ai|coustics API key.
"""

from livekit import agents
from livekit.agents import AgentSession, room_io
from livekit.plugins import ai_coustics


class VoiceGuardianAgent(agents.Agent):
    """Voice agent that listens to call center audio with noise cancellation."""

    def __init__(self):
        super().__init__(
            instructions=(
                "You are the Secure Voice Guardian agent. "
                "You listen to call center audio with real-time noise cancellation, "
                "transcribe speech, and help detect sensitive personal information."
            )
        )


async def entrypoint(ctx: agents.JobContext):
    """Main entrypoint for the LiveKit agent."""

    # Set up the agent session with ai|coustics VAD
    session = AgentSession(
        vad=ai_coustics.VAD(),
    )

    # Start the session with ai|coustics audio enhancement
    await session.start(
        agent=VoiceGuardianAgent(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                # ai|coustics real-time noise cancellation
                noise_cancellation=ai_coustics.audio_enhancement(
                    # QUAIL_VF_L: optimized for isolating the foreground speaker
                    # QUAIL_L: optimized for multiple speakers
                    model=ai_coustics.EnhancerModel.QUAIL_VF_L,
                    # Enhancement parameters
                    model_parameters=ai_coustics.ModelParameters(
                        # 0.5 = conservative (speech always preserved)
                        # 0.8 = balanced (optimal word error rate)
                        # 1.0 = aggressive (maximum suppression)
                        enhancement_level=0.8,
                    ),
                    # Voice Activity Detection settings
                    vad_settings=ai_coustics.VadSettings(
                        speech_hold_duration=0.03,   # 0.0 to 1.0 seconds
                        sensitivity=6.0,              # 1.0 to 15.0
                        minimum_speech_duration=0.0,  # 0.0 to 1.0 seconds
                    ),
                ),
            ),
        ),
    )


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint)
    )
