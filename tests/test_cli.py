import argparse
import asyncio
import base64
import json
from pathlib import Path

import pytest

import lingo.cli as cli
from lingo.cli import (
    CHUNK_SIZE,
    TranscriptState,
    _receive_events,
    _send_chunk,
    parser,
)


class FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


class FakeEventWebSocket:
    def __init__(self, events: list[dict]):
        self.events = iter(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return json.dumps(next(self.events))
        except StopIteration:
            raise StopAsyncIteration


def test_cli_requires_mp3_argument() -> None:
    with pytest.raises(SystemExit):
        parser().parse_args([])


@pytest.mark.asyncio
async def test_pcm_message_generation() -> None:
    websocket = FakeWebSocket()
    chunk = bytes(CHUNK_SIZE)

    await _send_chunk(websocket, chunk)

    assert json.loads(websocket.sent[0]) == {
        "type": "audio_chunk",
        "audio": base64.b64encode(chunk).decode("ascii"),
    }


@pytest.mark.asyncio
async def test_final_before_source_end_does_not_complete_stream() -> None:
    source_finished = asyncio.Event()
    transcript_state = TranscriptState()
    websocket = FakeEventWebSocket(
        [{"type": "final_transcript", "text": "first utterance"}]
    )

    with pytest.raises(RuntimeError, match="closed before final speech completed"):
        await _receive_events(websocket, source_finished, transcript_state)

    assert not transcript_state.final_observed
    assert transcript_state.latest_type == "final_transcript"


@pytest.mark.asyncio
async def test_final_after_source_end_completes_stream() -> None:
    source_finished = asyncio.Event()
    source_finished.set()
    transcript_state = TranscriptState()
    websocket = FakeEventWebSocket(
        [{"type": "final_transcript", "text": "last utterance"}]
    )

    with pytest.raises(RuntimeError, match="closed before final speech completed"):
        await _receive_events(websocket, source_finished, transcript_state)

    assert transcript_state.final_observed


@pytest.mark.asyncio
async def test_cli_waits_for_speech_and_writes_completed_clip(tmp_path) -> None:
    source_finished = asyncio.Event()
    source_finished.set()
    state = TranscriptState(output_dir=tmp_path)
    websocket = FakeEventWebSocket(
        [
            {"type": "final_transcript", "clip_id": "clip-1", "text": "hello"},
            {
                "type": "speech_start",
                "clip_id": "clip-1",
                "format": "mp3_44100_128",
            },
            {
                "type": "speech_chunk",
                "clip_id": "clip-1",
                "audio": base64.b64encode(b"one").decode(),
            },
            {
                "type": "speech_chunk",
                "clip_id": "clip-1",
                "audio": base64.b64encode(b"two").decode(),
            },
            {"type": "speech_end", "clip_id": "clip-1"},
        ]
    )

    with pytest.raises(RuntimeError, match="closed before final speech completed"):
        await _receive_events(websocket, source_finished, state)

    assert state.final_observed
    assert state.pending_clips == set()
    assert (tmp_path / "clip-1.mp3").read_bytes() == b"onetwo"


@pytest.mark.asyncio
async def test_cli_preserves_partial_clip_on_speech_error(tmp_path) -> None:
    source_finished = asyncio.Event()
    source_finished.set()
    state = TranscriptState(output_dir=tmp_path)
    websocket = FakeEventWebSocket(
        [
            {"type": "final_transcript", "clip_id": "clip-2", "text": "hello"},
            {
                "type": "speech_chunk",
                "clip_id": "clip-2",
                "audio": base64.b64encode(b"partial").decode(),
            },
            {
                "type": "speech_error",
                "clip_id": "clip-2",
                "code": 502,
                "message": "Speech generation unavailable",
            },
        ]
    )

    with pytest.raises(RuntimeError, match="closed before final speech completed"):
        await _receive_events(websocket, source_finished, state)

    assert state.final_observed
    assert state.pending_clips == set()
    assert (tmp_path / "clip-2.partial.mp3").read_bytes() == b"partial"


@pytest.mark.asyncio
async def test_empty_failed_speech_creates_no_file(tmp_path) -> None:
    source_finished = asyncio.Event()
    source_finished.set()
    state = TranscriptState(output_dir=tmp_path)
    websocket = FakeEventWebSocket(
        [
            {"type": "final_transcript", "clip_id": "clip-3", "text": "hello"},
            {
                "type": "speech_error",
                "clip_id": "clip-3",
                "code": 502,
                "message": "Speech generation unavailable",
            },
        ]
    )

    with pytest.raises(RuntimeError, match="closed before final speech completed"):
        await _receive_events(websocket, source_finished, state)

    assert state.final_observed
    assert state.pending_clips == set()
    assert list(tmp_path.iterdir()) == []


_CLOSED = object()


class LifecycleWebSocket:
    def __init__(self, producer=None):
        self.events: asyncio.Queue[object] = asyncio.Queue()
        self.producer = producer
        self.sent = 0
        self.closed = False

    async def recv(self) -> str:
        return json.dumps({"type": "ready", "session_id": "session"})

    async def send(self, message: str) -> None:
        self.sent += 1

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.events.get()
        if event is _CLOSED:
            raise StopAsyncIteration
        return json.dumps(event)

    async def __aenter__(self):
        if self.producer is not None:
            asyncio.create_task(self.producer(self.events))
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True


class FakeStdout:
    def __init__(self):
        self.chunks = iter((b"pcm", b""))

    async def read(self, size: int) -> bytes:
        return next(self.chunks)


class FakeProcess:
    def __init__(self):
        self.stdout = FakeStdout()
        self.returncode = 0

    def terminate(self) -> None:
        raise AssertionError("completed process must not be terminated")

    async def communicate(self):
        return b"", b""


def configure_transcribe(monkeypatch, websocket: LifecycleWebSocket) -> None:
    async def create_process(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(cli.shutil, "which", lambda command: "/usr/bin/ffmpeg")
    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(cli, "connect", lambda url: websocket)
    monkeypatch.setattr(cli, "TRAILING_SILENCE_SECONDS", 0)


@pytest.mark.asyncio
async def test_transcribe_waits_for_all_clips_without_timing_out(
    monkeypatch, tmp_path, capsys
) -> None:
    async def produce(events: asyncio.Queue[object]) -> None:
        await asyncio.sleep(0.005)
        await events.put(
            {"type": "final_transcript", "clip_id": "clip-1", "text": "one"}
        )
        await events.put({"type": "final_transcript", "text": "   "})
        await events.put(
            {
                "type": "speech_chunk",
                "clip_id": "clip-1",
                "audio": base64.b64encode(b"one").decode(),
            }
        )
        await events.put(
            {"type": "final_transcript", "clip_id": "clip-2", "text": "two"}
        )
        await events.put(
            {
                "type": "speech_chunk",
                "clip_id": "clip-2",
                "audio": base64.b64encode(b"two").decode(),
            }
        )
        await asyncio.sleep(0.03)
        await events.put({"type": "speech_end", "clip_id": "clip-1"})
        await asyncio.sleep(0.03)
        await events.put(
            {
                "type": "speech_error",
                "clip_id": "clip-2",
                "message": "Speech generation unavailable",
            }
        )

    websocket = LifecycleWebSocket(produce)
    configure_transcribe(monkeypatch, websocket)
    monkeypatch.chdir(tmp_path)
    Path("input.mp3").touch()

    await cli.transcribe(Path("input.mp3"), "ws://test", final_timeout=0.01)

    assert websocket.closed
    assert (tmp_path / "clip-1.mp3").read_bytes() == b"one"
    assert (tmp_path / "clip-2.partial.mp3").read_bytes() == b"two"
    assert "speech error for clip-2" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_transcribe_preserves_partial_audio_on_disconnect(
    monkeypatch, tmp_path
) -> None:
    async def produce(events: asyncio.Queue[object]) -> None:
        await asyncio.sleep(0)
        await events.put(
            {"type": "final_transcript", "clip_id": "clip-1", "text": "one"}
        )
        await events.put(
            {
                "type": "speech_chunk",
                "clip_id": "clip-1",
                "audio": base64.b64encode(b"partial").decode(),
            }
        )
        await events.put(_CLOSED)

    websocket = LifecycleWebSocket(produce)
    configure_transcribe(monkeypatch, websocket)
    monkeypatch.chdir(tmp_path)
    Path("input.mp3").touch()

    with pytest.raises(RuntimeError, match="closed before final speech completed"):
        await cli.transcribe(Path("input.mp3"), "ws://test", final_timeout=0.01)

    assert (tmp_path / "clip-1.partial.mp3").read_bytes() == b"partial"


@pytest.mark.asyncio
async def test_transcribe_times_out_only_before_final_commit(
    monkeypatch, tmp_path
) -> None:
    websocket = LifecycleWebSocket()
    configure_transcribe(monkeypatch, websocket)
    (tmp_path / "input.mp3").touch()

    with pytest.raises(TimeoutError, match="final transcript"):
        await cli.transcribe(tmp_path / "input.mp3", "ws://test", final_timeout=0.01)


@pytest.mark.asyncio
async def test_stale_final_does_not_satisfy_post_source_wait() -> None:
    state = TranscriptState(latest_type="final_transcript")

    with pytest.raises(TimeoutError, match="final transcript"):
        await cli._wait_for_completion(state, final_timeout=0.01)


@pytest.mark.asyncio
async def test_final_timeout_uses_one_absolute_deadline() -> None:
    state = TranscriptState()

    async def emit_activity() -> None:
        for _ in range(5):
            await asyncio.sleep(0.008)
            state.changed.set()

    activity = asyncio.create_task(emit_activity())
    started = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError, match="final transcript"):
        await cli._wait_for_completion(state, final_timeout=0.02)
    elapsed = asyncio.get_running_loop().time() - started
    activity.cancel()
    await asyncio.gather(activity, return_exceptions=True)

    assert elapsed < 0.04


def test_main_exits_successfully_after_final_speech_error(
    monkeypatch, capsys, tmp_path
) -> None:
    async def handled_speech_error(mp3, url, final_timeout) -> None:
        print("speech error for clip: Speech generation unavailable", file=cli.sys.stderr)

    class FakeParser:
        def parse_args(self):
            return argparse.Namespace(
                mp3=tmp_path / "input.mp3",
                url="ws://test",
                final_timeout=1.0,
            )

    monkeypatch.setattr(cli, "transcribe", handled_speech_error)
    monkeypatch.setattr(cli, "parser", FakeParser)

    assert cli.main() is None
    assert "speech error" in capsys.readouterr().err
