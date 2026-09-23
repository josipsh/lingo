import base64
import json
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from ..provider import (
    ProviderError,
    ProviderEvent,
    SpeechConfig,
    SpeechProvider,
    SpeechProviderError,
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


class ElevenLabsSpeechProvider(SpeechProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def synthesize(
        self, text: str, config: SpeechConfig
    ) -> AsyncIterator[bytes]:
        client = self._client or httpx.AsyncClient()
        request_id: str | None = None
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/{config.voice_id}/stream",
                params={"output_format": config.output_format},
                headers={
                    "xi-api-key": self._api_key,
                    "accept": "audio/mpeg",
                    "content-type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": config.model,
                    "language_code": config.language,
                },
            ) as response:
                request_id = response.headers.get("request-id") or response.headers.get(
                    "x-request-id"
                )
                if not response.is_success:
                    body = (await response.aread()).decode(errors="replace")
                    raise SpeechProviderError(
                        f"ElevenLabs TTS returned {response.status_code}: {body}",
                        request_id=request_id,
                    )
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk
        except SpeechProviderError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise SpeechProviderError(
                "ElevenLabs TTS request failed", request_id=request_id
            ) from exc
        finally:
            if self._client is None:
                await client.aclose()
