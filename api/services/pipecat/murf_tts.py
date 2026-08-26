"""Murf AI text-to-speech service — hosted inside the Dograh API package.

Placed here (instead of the pipecat submodule) so the Docker build does not
require pushing changes to an external pipecat fork.
"""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp
from loguru import logger
from websockets.asyncio.client import connect as websocket_connect
from websockets.protocol import State

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.settings import NOT_GIVEN, TTSSettings, _NotGiven
from pipecat.services.tts_service import InterruptibleTTSService, TextAggregationMode
from pipecat.utils.tracing.service_decorators import traced_tts


MURF_DEFAULT_BASE_URL = "wss://global.api.murf.ai/v1/speech/stream-input"
MURF_HTTP_STREAM_URL = "https://global.api.murf.ai/v1/speech/stream"
MURF_DEFAULT_MODEL = "falcon-2"
MURF_DEFAULT_VOICE = "Gordon"
MURF_DEFAULT_SAMPLE_RATE = 24000


@dataclass
class MurfTTSSettings(TTSSettings):
    """Settings for MurfTTSService.

    voice and model are inherited from TTSSettings (str | _NotGiven | None).
    murf_sample_rate is an extra field specific to Murf (8000 / 16000 / 24000).
    """

    murf_sample_rate: int = 24000


MURF_SHORT_VOICE_MAPPING = {
    "gordon": "en-US-gordon",
    "marcus": "en-US-marcus",
    "natalie": "en-US-natalie",
    "alicia": "en-US-alicia",
    "terrell": "en-US-terrell",
    "samantha": "en-US-samantha",
    "dylan": "en-US-dylan",
    "trevor": "en-US-trevor",
    "angela": "en-US-angela",
    "wayne": "en-US-wayne",
    "scarlett": "en-US-scarlett",
    "anusha": "hi-IN-anusha",
    "anisha": "hi-IN-anisha",
    "aarav": "hi-IN-aarav",
    "kabir": "hi-IN-kabir",
    "ananya": "hi-IN-ananya",
    "hazel": "en-UK-hazel",
}


def _normalize_murf_voice(voice_id: Any) -> str:
    """Normalize short display names to full Murf voice IDs (e.g. 'Alicia' -> 'en-US-alicia')."""
    if not voice_id or voice_id is NOT_GIVEN:
        return "en-US-gordon"
    s = str(voice_id).strip()
    s_lower = s.lower()
    if s_lower in MURF_SHORT_VOICE_MAPPING:
        return MURF_SHORT_VOICE_MAPPING[s_lower]
    return s


def _resolve_murf_locale(lang: Any, voice_id: str | None = None) -> str:
    if lang and lang is not NOT_GIVEN:
        s = str(lang).strip()
        mapping = {
            "te": "te-IN",
            "hi": "hi-IN",
            "ta": "ta-IN",
            "kn": "kn-IN",
            "bn": "bn-IN",
            "mr": "mr-IN",
            "en": "en-US",
            "pa": "pa-IN",
            "gu": "gu-IN",
            "ml": "ml-IN",
        }
        if s.lower() in mapping:
            return mapping[s.lower()]
        return s

    # Infer locale from voice_id prefix (e.g. "hi-IN-aarav" -> "hi-IN", "en-US-natalie" -> "en-US")
    if voice_id and "-" in voice_id:
        parts = voice_id.split("-")
        if len(parts) >= 3 and len(parts[0]) == 2 and len(parts[1]) == 2:
            return f"{parts[0]}-{parts[1]}"

    return "en-US"


def _normalize_murf_model(model: Any) -> str:
    """Murf only accepts ``falcon-2`` or ``gen2`` (lowercase)."""
    s = str(model or MURF_DEFAULT_MODEL).strip().lower()
    if s.startswith("falcon"):
        return "falcon-2"
    if s in ("gen2", "gen-2"):
        return "gen2"
    return "falcon-2"


def _pcm_without_wav_header(audio: bytes) -> bytes:
    if len(audio) > 44 and audio[:4] == b"RIFF":
        return audio[44:]
    return audio


class MurfTTSService(InterruptibleTTSService):
    """Murf AI real-time TTS service using WebSocket streaming (Falcon 2).

    Provides ultra-low-latency speech synthesis (~100 ms TTFA) via Murf AI's
    Falcon 2 model over a persistent WebSocket connection.
    """

    Settings = MurfTTSSettings

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = MURF_DEFAULT_BASE_URL,
        sample_rate: int | None = None,
        settings: MurfTTSSettings | None = None,
        **kwargs,
    ):
        default_settings = MurfTTSSettings(
            model=MURF_DEFAULT_MODEL,
            voice=MURF_DEFAULT_VOICE,
            murf_sample_rate=sample_rate or MURF_DEFAULT_SAMPLE_RATE,
        )

        if settings is not None:
            default_settings.apply_update(settings)

        effective_sample_rate = (
            sample_rate
            or (settings.murf_sample_rate if settings is not None else None)
            or MURF_DEFAULT_SAMPLE_RATE
        )

        super().__init__(
            push_stop_frames=True,
            push_start_frame=True,
            pause_frame_processing=True,
            sample_rate=effective_sample_rate,
            push_text_frames=True,
            text_aggregation_mode=kwargs.pop(
                "text_aggregation_mode", TextAggregationMode.SENTENCE
            ),
            stop_frame_timeout_s=0.8,
            settings=default_settings,
            **kwargs,
        )

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._receive_task = None
        self._keepalive_task = None
        self._use_http = False
        self._http_session: aiohttp.ClientSession | None = None

    def can_generate_metrics(self) -> bool:
        return True

    async def flush_audio(self, context_id: str | None = None):
        """Tell Murf this turn is complete so it flushes remaining audio."""
        if self._use_http or not self._websocket:
            return
        try:
            if self._websocket.state is not State.OPEN:
                return
            flush_id = context_id or self.get_active_audio_context_id()
            await self._websocket.send(
                json.dumps(self._build_text_msg("", context_id=flush_id, end=True))
            )
        except Exception as e:
            logger.warning(f"Murf TTS flush failed: {e}")

    def language_to_service_language(self, language) -> str | None:
        if language is None:
            return None
        return _resolve_murf_locale(language)

    def _murf_model(self) -> str:
        return _normalize_murf_model(self._settings.model)

    def _murf_voice(self) -> str:
        voice_id = self._settings.voice
        return _normalize_murf_voice(voice_id)

    def _websocket_url(self) -> str:
        query = urlencode(
            {
                "api-key": self._api_key,
                "model": self._murf_model(),
                "sample_rate": str(self.sample_rate or MURF_DEFAULT_SAMPLE_RATE),
                "channel_type": "MONO",
                "format": "PCM",
            }
        )
        return f"{self._base_url}?{query}"

    def _build_session_config(self) -> dict:
        voice = self._murf_voice()
        locale_str = _resolve_murf_locale(self._settings.language, voice)
        return {
            "voice_config": {
                "voiceId": voice,
                "model": self._murf_model(),
                "style": "Conversational",
                "locale": locale_str,
            }
        }

    def _build_text_msg(
        self,
        text: str,
        context_id: str | None = None,
        end: bool = False,
    ) -> dict:
        msg: dict[str, Any] = {"text": text, "end": end}
        if context_id:
            msg["context_id"] = context_id
        return msg

    async def start(self, frame: StartFrame):
        await super().start(frame)
        if not self._http_session or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=5, sock_read=30)
            )
        await self._connect()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None
        await self._disconnect()

    async def _update_settings(self, delta: TTSSettings) -> dict[str, Any]:
        return await super()._update_settings(delta)

    async def _connect(self):
        await super()._connect()
        await self._connect_websocket()
        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(self._receive_task_handler(self._report_error))
        if self._websocket and not self._keepalive_task:
            self._keepalive_task = self.create_task(self._keepalive_task_handler())

    async def _disconnect(self):
        await super()._disconnect()
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        if self._keepalive_task:
            await self.cancel_task(self._keepalive_task)
            self._keepalive_task = None
        await self._disconnect_websocket()

    async def _connect_websocket(self):
        if self._murf_model() == "gen2":
            self._use_http = True
            await self._call_event_handler("on_connected")
            return

        if self._use_http:
            await self._call_event_handler("on_connected")
            return
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return
            logger.debug("Connecting to Murf TTS")
            self._websocket = await websocket_connect(
                self._websocket_url(),
                additional_headers={"api-key": self._api_key},
            )
            config_msg = self._build_session_config()
            await self._websocket.send(json.dumps(config_msg))
            logger.debug(
                f"Murf TTS session configured: "
                f"voice={config_msg['voice_config']['voiceId']}, "
                f"model={config_msg['voice_config']['model']}"
            )
            await self._call_event_handler("on_connected")
        except Exception as e:
            err_str = str(e)
            if "1008" in err_str or "policy violation" in err_str.lower() or "Gen2" in err_str:
                logger.info(
                    f"Murf WebSocket restricted this voice/model ({err_str}). "
                    "Switching to Murf HTTP streaming."
                )
                self._use_http = True
                self._websocket = None
                await self._call_event_handler("on_connected")
                return
            await self.push_error(error_msg=f"Murf TTS connection error: {e}", exception=e)
            self._websocket = None
            await self._call_event_handler("on_connection_error", f"{e}")

    async def _disconnect_websocket(self):
        try:
            await self.stop_all_metrics()
            if self._websocket:
                logger.debug("Disconnecting from Murf TTS")
                await self._websocket.close()
        except Exception as e:
            await self.push_error(
                error_msg=f"Murf TTS error closing websocket: {e}", exception=e
            )
        finally:
            self._websocket = None
            await self._call_event_handler("on_disconnected")

    def _get_websocket(self):
        if self._websocket:
            return self._websocket
        raise Exception("Murf WebSocket not connected")

    async def _keepalive_task_handler(self):
        # Murf closes idle sockets after 3 minutes. A WebSocket ping does not
        # synthesize audio, unlike sending ``{"text": " "}``.
        while True:
            await asyncio.sleep(30)
            if self._websocket and self._websocket.state is State.OPEN:
                try:
                    await self._websocket.ping()
                except Exception as e:
                    logger.warning(f"Murf TTS keepalive error: {e}")

    async def _receive_messages(self):
        async for message in self._get_websocket():
            try:
                msg = json.loads(message)
            except Exception:
                logger.warning(f"Murf TTS: failed to parse message: {message!r}")
                continue

            if "audio" in msg and msg.get("audio"):
                await self.stop_ttfb_metrics()
                context_id = msg.get("context_id") or self.get_active_audio_context_id()
                audio = _pcm_without_wav_header(base64.b64decode(msg["audio"]))
                if audio:
                    frame = TTSAudioRawFrame(
                        audio=audio,
                        sample_rate=self.sample_rate,
                        num_channels=1,
                        context_id=context_id,
                    )
                    await self.append_to_audio_context(context_id, frame)
                continue

            if msg.get("final"):
                context_id = msg.get("context_id") or self.get_active_audio_context_id()
                await self.stop_all_metrics()
                if context_id and self.audio_context_available(context_id):
                    await self.append_to_audio_context(
                        context_id, TTSStoppedFrame(context_id=context_id)
                    )
                    await self.remove_audio_context(context_id)
                continue

            error_msg = msg.get("message") or msg.get("error")
            if error_msg or msg.get("event") == "error":
                context_id = self.get_active_audio_context_id()
                await self.push_frame(TTSStoppedFrame(context_id=context_id))
                await self.stop_all_metrics()
                await self.push_error(error_msg=f"Murf TTS error: {error_msg or msg}")
                continue

            logger.debug(f"Murf TTS: unhandled message: {msg}")

    async def _stream_http_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        headers = {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }
        voice = self._murf_voice()
        locale_str = _resolve_murf_locale(self._settings.language, voice)
        data = {
            "voiceId": voice,
            "style": "Conversational",
            "text": text,
            "model": self._murf_model(),
            "format": "PCM",
            "sampleRate": self.sample_rate or MURF_DEFAULT_SAMPLE_RATE,
            "channelType": "MONO",
            "locale": locale_str,
        }

        await self.start_ttfb_metrics()
        await self.start_tts_usage_metrics(text)

        session = self._http_session
        close_session = False
        if not session or session.closed:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=5, sock_read=30)
            )
            close_session = True

        try:
            async with session.post(MURF_HTTP_STREAM_URL, headers=headers, json=data) as resp:
                if resp.status != 200:
                    err_body = await resp.text()
                    logger.error(f"Murf HTTP streaming returned {resp.status}: {err_body}")
                    yield ErrorFrame(error=f"Murf HTTP TTS error: {err_body[:200]}")
                    yield TTSStoppedFrame(context_id=context_id)
                    await self.stop_all_metrics()
                    return

                await self.stop_ttfb_metrics()
                async for frame in self._stream_audio_frames_from_iterator(
                    resp.content.iter_chunked(4096),
                    strip_wav_header=True,
                    in_sample_rate=self.sample_rate,
                    context_id=context_id,
                ):
                    yield frame

                await self.stop_all_metrics()
                yield TTSStoppedFrame(context_id=context_id)
        except Exception as e:
            logger.error(f"Murf HTTP streaming exception: {e}")
            yield ErrorFrame(error=f"Murf HTTP TTS error: {e}")
            yield TTSStoppedFrame(context_id=context_id)
            await self.stop_all_metrics()
        finally:
            if close_session and session and not session.closed:
                await session.close()

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        logger.debug(f"{self}: Generating TTS [{text}]")
        if self._use_http:
            async for frame in self._stream_http_tts(text, context_id):
                yield frame
            return

        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()
            if self._use_http:
                async for frame in self._stream_http_tts(text, context_id):
                    yield frame
                return
            try:
                await self.start_ttfb_metrics()
                await self._get_websocket().send(
                    json.dumps(self._build_text_msg(text, context_id=context_id, end=False))
                )
                await self.start_tts_usage_metrics(text)
            except Exception as e:
                logger.warning(f"Murf WebSocket send failed ({e}), falling back to HTTP stream")
                self._use_http = True
                async for frame in self._stream_http_tts(text, context_id):
                    yield frame
                return
            yield None
        except Exception as e:
            logger.warning(f"Murf WebSocket error ({e}), falling back to HTTP stream")
            self._use_http = True
            async for frame in self._stream_http_tts(text, context_id):
                yield frame
