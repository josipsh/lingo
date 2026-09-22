import base64
import json
from collections.abc import AsyncIterator
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from ..provider import (
    ProviderError,
    ProviderEvent,
    TranscriptionConfig,
    TranscriptionProvider,
    TranscriptionSession,
)

SAMPLE_RATE = 16_000

ERROR_EVENTS = {
    "error",
    "auth_error",
    "quota_exceeded",
    "commit_throttled",
    "unaccepted_terms",
    "rate_limited",
    "queue_overflow",
    "resource_exhausted",
    "session_time_limit_exceeded",
    "input_error",
    "invalid_request",
    "chunk_size_exceeded",
    "insufficient_audio_activity",
    "transcriber_error",
}


class ElevenLabsSession(TranscriptionSession):
    def __init__(self, websocket: ClientConnection):
        self._websocket = websocket

    async def send_audio(self, audio: bytes) -> None:
        payload = {
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(audio).decode("ascii"),
            "commit": False,
            "sample_rate": SAMPLE_RATE,
        }
        try:
            await self._websocket.send(json.dumps(payload))
        except (ConnectionClosed, WebSocketException) as exc:
            raise ProviderError("ElevenLabs audio send failed") from exc

    async def events(self) -> AsyncIterator[ProviderEvent]:
        try:
            async for raw_message in self._websocket:
                try:
                    message = json.loads(raw_message)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ProviderError("ElevenLabs returned invalid JSON") from exc

                message_type = message.get("message_type")
                if message_type == "session_started":
                    session_id = message.get("session_id")
                    if not isinstance(session_id, str) or not session_id:
                        raise ProviderError("ElevenLabs omitted the session identifier")
                    yield ProviderEvent("ready", session_id=session_id)
                elif message_type == "partial_transcript":
                    yield ProviderEvent("partial", text=_text(message))
                elif message_type in {
                    "committed_transcript",
                    "committed_transcript_with_timestamps",
                }:
                    yield ProviderEvent("final", text=_text(message))
                elif message_type in ERROR_EVENTS:
                    raise ProviderError(f"ElevenLabs error event: {message_type}")
        except ProviderError:
            raise
        except (ConnectionClosed, WebSocketException) as exc:
            raise ProviderError("ElevenLabs connection closed unexpectedly") from exc

    async def close(self) -> None:
        await self._websocket.close()


def _text(message: object) -> str:
    if not isinstance(message, dict) or not isinstance(message.get("text"), str):
        raise ProviderError("ElevenLabs returned an invalid transcript")
    return message["text"]


class ElevenLabsProvider(TranscriptionProvider):
    def __init__(self, api_key: str, websocket_url: str):
        self._api_key = api_key
        self._websocket_url = websocket_url

    async def connect(self, config: TranscriptionConfig) -> ElevenLabsSession:
        query = urlencode(
            {
                "model_id": config.model,
                "language_code": config.language,
                "audio_format": config.audio_format,
                "commit_strategy": config.commit_strategy,
            }
        )
        separator = "&" if "?" in self._websocket_url else "?"
        try:
            websocket = await connect(
                f"{self._websocket_url}{separator}{query}",
                additional_headers={"xi-api-key": self._api_key},
            )
        except (OSError, WebSocketException) as exc:
            raise ProviderError("Could not connect to ElevenLabs") from exc
        return ElevenLabsSession(websocket)
