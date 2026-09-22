import asyncio
import base64
import json

import pytest

from lingo.cli import CHUNK_SIZE, TranscriptState, _receive_events, _send_chunk, parser


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
    final_after_source = asyncio.Event()
    transcript_state = TranscriptState()
    websocket = FakeEventWebSocket(
        [{"type": "final_transcript", "text": "first utterance"}]
    )

    with pytest.raises(RuntimeError, match="closed before the final transcript"):
        await _receive_events(
            websocket, source_finished, final_after_source, transcript_state
        )

    assert not final_after_source.is_set()
    assert transcript_state.latest_type == "final_transcript"


@pytest.mark.asyncio
async def test_final_after_source_end_completes_stream() -> None:
    source_finished = asyncio.Event()
    source_finished.set()
    final_after_source = asyncio.Event()
    transcript_state = TranscriptState()
    websocket = FakeEventWebSocket(
        [{"type": "final_transcript", "text": "last utterance"}]
    )

    with pytest.raises(RuntimeError, match="closed before the final transcript"):
        await _receive_events(
            websocket, source_finished, final_after_source, transcript_state
        )

    assert final_after_source.is_set()
