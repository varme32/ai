"""Murf AI text-to-speech service — hosted inside the Dograh API package.

Placed here (instead of the pipecat submodule) so the Docker build does not
require pushing changes to an external pipecat fork.  The implementation is
identical to what would live in pipecat/src/pipecat/services/murf/tts.py.
"""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

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
from pipecat.services.tts_service import InterruptibleTTSService
from pipecat.utils.tracing.service_decorators import traced_tts


MURF_DEFAULT_BASE_URL = "wss://global.api.murf.ai/v1/speech/stream-input"
MURF_DEFAULT_MODEL = "FALCON"
MURF_DEFAULT_VOICE = "Gordon"
MURF_DEFAULT_SAMPLE_RATE = 24000


@dataclass
class MurfTTSSettings(TTSSettings):
    """Settings for MurfTTSService.

    voice and model are inherited from TTSSettings (str | _NotGiven | None).
    murf_sample_rate is an extra field specific to Murf (8000 / 16000 / 24000).
    """

    murf_sample_rate: int = 24000


class MurfTTSService(InterruptibleTTSService):
    """Murf AI real-time TTS service using WebSocket streaming (Falcon 2).

    Provides ultra-low-latency speech synthesis (~100 ms TTFA) via Murf AI's
    Falcon 2 model over a persistent WebSocket connection.

    Example::

        tts = MurfTTSService(
            api_key="your-murf-api-key",
            settings=MurfTTSSettings(voice="Gordon", model="falcon-2"),
        )
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
            murf_sample_rate=sample_rate or 24000,
        )

        if settings is not None:
            default_settings.apply_update(settings)

        effective_sample_rate = (
            sample_rate
            or (settings.murf_sample_rate if settings is not None else None)
            or 24000
        )

        super().__init__(
            push_stop_frames=True,
            push_start_frame=True,
            pause_frame_processing=True,
            sample_rate=effective_sample_rate,
            push_text_frames=True,
            settings=default_settings,
            **kwargs,
        )

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._receive_task = None
        self._keepalive_task = None
        self._use_http = False

    def can_generate_metrics(self) -> bool:
        return True

    async def flush_audio(self, context_id: str | None = None):
        logger.trace(f"{self}: flushing audio")

    def language_to_service_language(self, language) -> str | None:
        if language is None:
            return None
        return str(language).split("-")[0]

    def _build_session_config(self) -> dict:
        voice_id = self._settings.voice
        if voice_id is NOT_GIVEN or not voice_id:
            voice_id = MURF_DEFAULT_VOICE
        model = self._settings.model
        if model is NOT_GIVEN or not model:
            model = "FALCON"
        elif str(model).lower().startswith("falcon"):
            model = "FALCON"

        voice_config = {
            "voice_id": voice_id,
            "voiceId": voice_id,
            "model": model,
            "style": "Conversational",
            "sample_rate": self._settings.murf_sample_rate or 24000,
            "sampleRate": self._settings.murf_sample_rate or 24000,
            "format": "PCM",
            "channel_type": "MONO",
            "channelType": "MONO",
        }
        if self._settings.language and self._settings.language is not NOT_GIVEN:
            lang_str = str(self._settings.language)
            voice_config["locale"] = lang_str
            voice_config["multiNativeLocale"] = lang_str

        return {
            "voice_config": voice_config
        }

    def _build_text_msg(self, text: str) -> dict:
        return {"text": text}

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
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
        model_str = str(self._settings.model or "").upper()
        if model_str == "GEN2":
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
                self._base_url,
                additional_headers={"api-key": self._api_key},
            )
            config_msg = self._build_session_config()
            await self._websocket.send(json.dumps(config_msg))
            logger.debug(
                f"Murf TTS session configured: "
                f"voice={config_msg['voice_config']['voice_id']}, "
                f"model={config_msg['voice_config']['model']}"
            )
            await self._call_event_handler("on_connected")
        except Exception as e:
            err_str = str(e)
            if "1008" in err_str or "policy violation" in err_str.lower() or "Gen2" in err_str:
                logger.info(
                    f"Murf WebSocket restricted this voice/model ({err_str}). "
                    "Switching to Murf HTTP streaming to support all Telugu & Gen2 voices."
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
        while True:
            await asyncio.sleep(30)
            if self._websocket and self._websocket.state is State.OPEN:
                try:
                    await self._websocket.send(json.dumps({"text": " "}))
                except Exception as e:
                    logger.warning(f"Murf TTS keepalive error: {e}")

    async def _receive_messages(self):
        async for message in self._get_websocket():
            try:
                msg = json.loads(message)
            except Exception:
                logger.warning(f"Murf TTS: failed to parse message: {message!r}")
                continue

            event = msg.get("event") or msg.get("type") or msg.get("status")

            if event in ("audio", "chunk"):
                await self.stop_ttfb_metrics()
                context_id = self.get_active_audio_context_id()
                audio_b64 = msg.get("audio") or (msg.get("data") or {}).get("audio")
                if audio_b64:
                    frame = TTSAudioRawFrame(
                        audio=base64.b64decode(audio_b64),
                        sample_rate=self.sample_rate,
                        num_channels=1,
                        context_id=context_id,
                    )
                    await self.append_to_audio_context(context_id, frame)

            elif event in ("complete", "done", "end"):
                await self.stop_all_metrics()

            elif event == "error":
                context_id = self.get_active_audio_context_id()
                await self.push_frame(TTSStoppedFrame(context_id=context_id))
                await self.stop_all_metrics()
                error_msg = msg.get("message") or msg.get("error") or str(msg)
                await self.push_error(error_msg=f"Murf TTS error: {error_msg}")

            else:
                logger.debug(f"Murf TTS: unhandled message: {msg}")

    async def _stream_http_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        url = "https://api.murf.ai/v1/speech/stream"
        headers = {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }
        voice_id = self._settings.voice or MURF_DEFAULT_VOICE
        model_str = str(self._settings.model or "GEN2").upper()
        if "FALCON" in model_str:
            model = "Falcon-2"
        else:
            model = "GEN2"

        data = {
            "voice_id": str(voice_id),
            "style": "Conversational",
            "text": text,
            "model": model,
            "format": "PCM",
            "sampleRate": self.sample_rate or 24000,
            "channelType": "MONO",
        }
        if self._settings.language and self._settings.language is not NOT_GIVEN:
            lang_str = str(self._settings.language)
            data["locale"] = lang_str
            data["multiNativeLocale"] = lang_str

        await self.start_ttfb_metrics()
        await self.start_tts_usage_metrics(text)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data) as resp:
                    if resp.status != 200:
                        err_body = await resp.text()
                        logger.error(f"Murf HTTP streaming returned {resp.status}: {err_body}")
                        yield ErrorFrame(error=f"Murf HTTP TTS error: {err_body[:200]}")
                        yield TTSStoppedFrame(context_id=context_id)
                        await self.stop_all_metrics()
                        return

                    await self.stop_ttfb_metrics()
                    chunk_size = 2048
                    while True:
                        chunk = await resp.content.read(chunk_size)
                        if not chunk:
                            break
                        frame = TTSAudioRawFrame(
                            audio=chunk,
                            sample_rate=self.sample_rate,
                            num_channels=1,
                            context_id=context_id,
                        )
                        yield frame

                    await self.stop_all_metrics()
                    yield TTSStoppedFrame(context_id=context_id)
        except Exception as e:
            logger.error(f"Murf HTTP streaming exception: {e}")
            yield ErrorFrame(error=f"Murf HTTP TTS error: {e}")
            yield TTSStoppedFrame(context_id=context_id)
            await self.stop_all_metrics()

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
                await self._get_websocket().send(json.dumps(self._build_text_msg(text)))
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
