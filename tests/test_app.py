import asyncio
import base64
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lingo.main import create_app
from lingo.provider import (
    ProviderError,
    ProviderEvent,
    SpeechConfig,
    TranscriptionConfig,
)

CONFIG = TranscriptionConfig("scribe_v2_realtime", "en", "pcm_16000")
SPEECH_CONFIG = SpeechConfig(
    "eleven_flash_v2_5", "voice-123", "en", "mp3_44100_128"
)


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


@dataclass
class FakeSpeechProvider:
    chunks: tuple[bytes, ...] = (b"mp3-1", b"mp3-2")
    calls: list[tuple[str, SpeechConfig]] = field(default_factory=list)
    fail_text: str | None = None

    async def synthesize(self, text: str, config: SpeechConfig):
        self.calls.append((text, config))
        yield self.chunks[0]
        if text == self.fail_text:
            raise ProviderError("private speech failure")
        for chunk in self.chunks[1:]:
            yield chunk


@dataclass
class BlockingSpeechProvider:
    calls: list[str] = field(default_factory=list)
    first_started: threading.Event = field(default_factory=threading.Event)
    release_first: threading.Event = field(default_factory=threading.Event)
    first_cancelled: threading.Event = field(default_factory=threading.Event)

    async def synthesize(self, text: str, config: SpeechConfig):
        self.calls.append(text)
        if text == "first":
            self.first_started.set()
            try:
                while not self.release_first.is_set():
                    await asyncio.sleep(0.01)
            finally:
                if not self.release_first.is_set():
                    self.first_cancelled.set()
        yield text.encode()


def make_app(provider, speech_provider=None):
    return create_app(
        provider,
        CONFIG,
        speech_provider or FakeSpeechProvider(),
        SPEECH_CONFIG,
    )


def test_transcription_flow_and_cleanup() -> None:
    session = FakeSession()
    provider = FakeProvider(session)
    speech_provider = FakeSpeechProvider()
    app = make_app(provider, speech_provider)

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
        final = websocket.receive_json()
        assert final == {
            "type": "final_transcript",
            "clip_id": final["clip_id"],
            "text": "hello world",
        }
        clip_id = final["clip_id"]
        assert websocket.receive_json() == {
            "type": "speech_start",
            "clip_id": clip_id,
            "format": "mp3_44100_128",
        }
        assert websocket.receive_json() == {
            "type": "speech_chunk",
            "clip_id": clip_id,
            "audio": base64.b64encode(b"mp3-1").decode(),
        }
        assert websocket.receive_json() == {
            "type": "speech_chunk",
            "clip_id": clip_id,
            "audio": base64.b64encode(b"mp3-2").decode(),
        }
        assert websocket.receive_json() == {"type": "speech_end", "clip_id": clip_id}

    assert session.audio == [b"pcm"]
    assert session.closed
    assert provider.received_config == CONFIG
    assert speech_provider.calls == [("hello world", SPEECH_CONFIG)]


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
    app = make_app(FakeProvider(session))

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
    app = make_app(FakeProvider(session))

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
    app = make_app(FakeProvider(FakeSession(fail=True)))

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
    app = make_app(provider)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        websocket.close()
        assert provider.cancelled.wait(timeout=1)


def test_whitespace_final_does_not_generate_speech() -> None:
    class WhitespaceSession(FakeSession):
        async def events(self):
            yield ProviderEvent("ready", session_id="session-123")
            yield ProviderEvent("final", text="   ")
            await asyncio.Event().wait()

    speech_provider = FakeSpeechProvider()
    app = make_app(FakeProvider(WhitespaceSession()), speech_provider)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        assert websocket.receive_json() == {"type": "final_transcript", "text": "   "}

    assert speech_provider.calls == []


def test_speech_failure_is_safe_and_next_clip_runs() -> None:
    class TwoFinalsSession(FakeSession):
        async def events(self):
            yield ProviderEvent("ready", session_id="session-123")
            yield ProviderEvent("final", text="first")
            yield ProviderEvent("final", text="second")
            await asyncio.Event().wait()

    speech_provider = FakeSpeechProvider(fail_text="first")
    app = make_app(FakeProvider(TwoFinalsSession()), speech_provider)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        events = [websocket.receive_json() for _ in range(9)]

    finals = [event for event in events if event["type"] == "final_transcript"]
    first_id, second_id = [event["clip_id"] for event in finals]
    assert next(event for event in events if event["type"] == "speech_error") == {
        "type": "speech_error",
        "clip_id": first_id,
        "code": 502,
        "message": "Speech generation unavailable",
    }
    assert {"type": "speech_end", "clip_id": second_id} in events
    assert [text for text, _ in speech_provider.calls] == ["first", "second"]


def test_transcription_continues_while_speech_jobs_run_serially() -> None:
    class ResponsiveSession(FakeSession):
        async def events(self):
            yield ProviderEvent("ready", session_id="session-123")
            while len(self.audio) < 1:
                await asyncio.sleep(0)
            yield ProviderEvent("final", text="first")
            while len(self.audio) < 2:
                await asyncio.sleep(0)
            yield ProviderEvent("partial", text="sec")
            yield ProviderEvent("final", text="second")
            await asyncio.Event().wait()

    speech_provider = BlockingSpeechProvider()
    app = make_app(FakeProvider(ResponsiveSession()), speech_provider)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        websocket.send_json({"type": "audio_chunk", "audio": "b25l"})
        first_events = [websocket.receive_json() for _ in range(2)]
        assert {event["type"] for event in first_events} == {
            "final_transcript",
            "speech_start",
        }
        assert speech_provider.first_started.wait(timeout=1)

        websocket.send_json({"type": "audio_chunk", "audio": "dHdv"})
        assert websocket.receive_json() == {
            "type": "partial_transcript",
            "text": "sec",
        }
        second_final = websocket.receive_json()
        assert second_final["type"] == "final_transcript"
        assert speech_provider.calls == ["first"]

        speech_provider.release_first.set()
        terminal_events = [websocket.receive_json() for _ in range(5)]

    assert [event["type"] for event in terminal_events] == [
        "speech_chunk",
        "speech_end",
        "speech_start",
        "speech_chunk",
        "speech_end",
    ]
    assert speech_provider.calls == ["first", "second"]


def test_disconnect_cancels_active_speech_and_discards_queue() -> None:
    class TwoFinalsSession(FakeSession):
        async def events(self):
            yield ProviderEvent("ready", session_id="session-123")
            yield ProviderEvent("final", text="first")
            yield ProviderEvent("final", text="second")
            await asyncio.Event().wait()

    speech_provider = BlockingSpeechProvider()
    app = make_app(FakeProvider(TwoFinalsSession()), speech_provider)

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        events = [websocket.receive_json() for _ in range(3)]
        assert sum(event["type"] == "final_transcript" for event in events) == 2
        assert sum(event["type"] == "speech_start" for event in events) == 1
        websocket.close()

    assert speech_provider.first_cancelled.wait(timeout=1)
    assert speech_provider.calls == ["first"]


def test_disconnect_cancels_blocked_audio_send() -> None:
    class BlockingSendSession(FakeSession):
        send_started = threading.Event()
        send_cancelled = threading.Event()

        async def send_audio(self, audio: bytes) -> None:
            self.send_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.send_cancelled.set()

        async def events(self):
            yield ProviderEvent("ready", session_id="session-123")
            await asyncio.Event().wait()

    session = BlockingSendSession()
    app = make_app(FakeProvider(session))

    with TestClient(app).websocket_connect("/ws/transcription") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        websocket.send_json({"type": "audio_chunk", "audio": "cGNt"})
        assert session.send_started.wait(timeout=1)
        websocket.close()

    assert session.send_cancelled.wait(timeout=1)
