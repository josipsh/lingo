import asyncio
import base64
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lingo.main import create_app
from lingo.provider import ProviderError, ProviderEvent, TranscriptionConfig

CONFIG = TranscriptionConfig("scribe_v2_realtime", "en", "pcm_16000")


@dataclass
class FakeSession:
    fail: bool = False
    audio: list[bytes] = field(default_factory=list)
    closed: bool = False

    async def send_audio(self, audio: bytes) -> None:
        self.audio.append(audio)

    async def events(self) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent("ready", session_id="session-123")
        if self.fail:
            raise ProviderError("paid provider details must remain private")
        while not self.audio:
            await asyncio.sleep(0)
        yield ProviderEvent("partial", text="hello")
        yield ProviderEvent("final", text="hello world")
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeProvider:
    session: FakeSession
    received_config: TranscriptionConfig | None = None

    async def connect(self, config: TranscriptionConfig) -> FakeSession:
        self.received_config = config
        return self.session


@dataclass
class SlowProvider:
    cancelled: threading.Event = field(default_factory=threading.Event)

    async def connect(self, config: TranscriptionConfig) -> FakeSession:
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


def test_transcription_flow_and_cleanup() -> None:
    session = FakeSession()
    provider = FakeProvider(session)
    app = create_app(provider, CONFIG)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        assert websocket.receive_json() == {
            "type": "ready",
            "session_id": "session-123",
        }
        websocket.send_json(
            {
                "type": "audio_chunk",
                "audio": base64.b64encode(b"pcm").decode(),
            }
        )
        assert websocket.receive_json() == {
            "type": "partial_transcript",
            "text": "hello",
        }
        assert websocket.receive_json() == {
            "type": "final_transcript",
            "text": "hello world",
        }

    assert session.audio == [b"pcm"]
    assert session.closed
    assert provider.received_config == CONFIG


@pytest.mark.parametrize(
    ("message", "expected_message"),
    [
        ("not json", "Message must be valid JSON"),
        ("[]", "Message must be a JSON object"),
        ('{"type":"other","audio":"cGNt"}', "Unsupported message type"),
        ('{"type":"audio_chunk"}', "Audio must be a base64 string"),
        ('{"type":"audio_chunk","audio":1}', "Audio must be a base64 string"),
        ('{"type":"audio_chunk","audio":"%%%"}', "Audio must be valid base64"),
        ('{"type":"audio_chunk","audio":""}', "Audio must not be empty"),
    ],
)
def test_bad_message_is_recoverable(message: str, expected_message: str) -> None:
    session = FakeSession()
    app = create_app(FakeProvider(session), CONFIG)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        websocket.send_text(message)
        assert websocket.receive_json() == {
            "type": "error",
            "code": 400,
            "message": expected_message,
        }
        websocket.send_json({"type": "audio_chunk", "audio": "cGNt"})
        assert websocket.receive_json()["type"] == "partial_transcript"

    assert session.audio == [b"pcm"]


def test_binary_message_is_recoverable() -> None:
    session = FakeSession()
    app = create_app(FakeProvider(session), CONFIG)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        websocket.send_bytes(b"not json")
        assert websocket.receive_json() == {
            "type": "error",
            "code": 400,
            "message": "Message must be a text JSON message",
        }
        websocket.send_json({"type": "audio_chunk", "audio": "cGNt"})
        assert websocket.receive_json()["type"] == "partial_transcript"

    assert session.audio == [b"pcm"]


def test_provider_failure_is_safe_and_fatal() -> None:
    app = create_app(FakeProvider(FakeSession(fail=True)), CONFIG)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        assert websocket.receive_json() == {
            "type": "error",
            "code": 502,
            "message": "Transcription service unavailable",
        }
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1011


def test_disconnect_cancels_provider_connection() -> None:
    provider = SlowProvider()
    app = create_app(provider, CONFIG)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        websocket.close()
        assert provider.cancelled.wait(timeout=1)
