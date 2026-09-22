import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest
from websockets.asyncio.server import serve

from lingo.provider import ProviderError, TranscriptionConfig
from lingo.providers.elevenlabs import ElevenLabsProvider, ElevenLabsSession


class FakeWebSocket:
    def __init__(self, messages=()):
        self.messages = iter(messages)
        self.sent: list[str] = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise StopAsyncIteration

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_audio_is_sent_in_elevenlabs_wire_format() -> None:
    websocket = FakeWebSocket()
    session = ElevenLabsSession(websocket)  # type: ignore[arg-type]

    await session.send_audio(b"pcm")

    assert json.loads(websocket.sent[0]) == {
        "message_type": "input_audio_chunk",
        "audio_base_64": base64.b64encode(b"pcm").decode(),
        "commit": False,
        "sample_rate": 16000,
    }


@pytest.mark.asyncio
async def test_events_are_normalized() -> None:
    websocket = FakeWebSocket(
        [
            '{"message_type":"session_started","session_id":"abc","config":{}}',
            '{"message_type":"partial_transcript","text":"hel"}',
            '{"message_type":"committed_transcript","text":"hello"}',
        ]
    )
    session = ElevenLabsSession(websocket)  # type: ignore[arg-type]

    events = [event async for event in session.events()]

    assert [event.type for event in events] == ["ready", "partial", "final"]
    assert events[0].session_id == "abc"
    assert events[2].text == "hello"


@pytest.mark.asyncio
async def test_provider_error_payload_is_not_exposed() -> None:
    websocket = FakeWebSocket(
        ['{"message_type":"quota_exceeded","error":"sensitive detail"}']
    )
    session = ElevenLabsSession(websocket)  # type: ignore[arg-type]

    with pytest.raises(ProviderError, match="quota_exceeded") as exc_info:
        _ = [event async for event in session.events()]

    assert "sensitive detail" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_provider_connection_uses_config_and_api_key() -> None:
    request_details = {}

    async def handler(websocket) -> None:
        request_details["path"] = websocket.request.path
        request_details["api_key"] = websocket.request.headers["xi-api-key"]
        await websocket.send(
            '{"message_type":"session_started","session_id":"abc","config":{}}'
        )
        await websocket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        provider = ElevenLabsProvider("secret", f"ws://127.0.0.1:{port}/realtime")
        config = TranscriptionConfig(
            model="configured-model",
            language="en",
            audio_format="pcm_16000",
        )

        session = await provider.connect(config)
        event = await anext(session.events())
        await session.close()

    parsed_url = urlparse(request_details["path"])
    assert request_details["api_key"] == "secret"
    assert parse_qs(parsed_url.query) == {
        "model_id": ["configured-model"],
        "language_code": ["en"],
        "audio_format": ["pcm_16000"],
        "commit_strategy": ["vad"],
    }
    assert event.session_id == "abc"
