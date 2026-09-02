"""Shared helpers for tuning pipecat ``TransportParams`` per run mode.

These live outside ``transport_setup.py`` (which is non-telephony only) so
that both the WebRTC factory there and the telephony provider factories
under ``api.services.telephony.providers/<name>/transport.py`` can call
into the same place.
"""

# Pipecat's TransportParams default is 3s. While the TTS queue is empty
# (waiting on the next LLM sentence) that timeout is how long the bot is
# still considered "speaking", which mutes the user and sounds like a
# pause-then-play glitch. 0.25s covers a real TTS gap without holding
# the "bot is speaking" mute for half a second after every sentence.
CALL_BOT_VAD_STOP_SECS = 0.25
# Kept as an alias so existing realtime imports keep working.
REALTIME_BOT_VAD_STOP_SECS = CALL_BOT_VAD_STOP_SECS

# 20 ms chunks instead of the 40 ms default. Smaller frames keep the
# telephony websocket fed more evenly and cut playout delay.
CALL_AUDIO_OUT_10MS_CHUNKS = 2


def realtime_param_overrides(is_realtime: bool) -> dict:
    """Return kwargs to splat into ``TransportParams`` for every call.

    ``is_realtime`` is accepted so existing call sites keep working; the
    output-pacing knobs apply to both cascaded and speech-to-speech runs.
    """
    return {
        "bot_vad_stop_secs": CALL_BOT_VAD_STOP_SECS,
        "audio_out_10ms_chunks": CALL_AUDIO_OUT_10MS_CHUNKS,
    }
