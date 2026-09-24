import asyncio
from unittest.mock import AsyncMock

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    ErrorFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from lingo.processors import AssistantTurnRecorder, LLMFailureHandler, TurnWriter


def test_llm_error_sends_fallback_toward_tts():
    async def run():
        llm = FrameProcessor()
        callback = AsyncMock()
        handler = LLMFailureHandler("Call again.", callback, llm)
        handler.push_frame = AsyncMock()

        await handler.process_frame(
            ErrorFrame(error="failed", processor=llm), FrameDirection.UPSTREAM
        )

        frame, direction = handler.push_frame.await_args.args
        assert isinstance(frame, TTSSpeakFrame)
        assert frame.text == "Call again."
        assert direction is FrameDirection.DOWNSTREAM
        callback.assert_awaited_once()
        assert handler.failed is True
        await handler.cleanup()

    asyncio.run(run())


def test_assistant_turn_is_saved_only_after_transport_finishes():
    class Writer:
        def __init__(self):
            self.turns = []

        def record(self, role, content):
            self.turns.append((role, content))

    async def run():
        writer = Writer()
        recorder = AssistantTurnRecorder(writer)
        recorder.push_frame = AsyncMock()
        await recorder.process_frame(LLMTextFrame("Hello"), FrameDirection.DOWNSTREAM)
        await recorder.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        assert writer.turns == []

        await recorder.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        assert writer.turns == [("assistant", "Hello")]
        await recorder.cleanup()

    asyncio.run(run())


def test_interrupted_assistant_turn_is_not_saved():
    class Writer:
        def __init__(self):
            self.turns = []

        def record(self, role, content):
            self.turns.append((role, content))

    async def run():
        writer = Writer()
        recorder = AssistantTurnRecorder(writer)
        recorder.push_frame = AsyncMock()
        recorder._start_interruption = AsyncMock()
        await recorder.process_frame(LLMTextFrame("Partial"), FrameDirection.DOWNSTREAM)
        await recorder.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        await recorder.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        await recorder.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        assert writer.turns == []
        await recorder.cleanup()

    asyncio.run(run())


def test_disconnect_wait_captures_in_flight_transport_completion():
    class Writer:
        def __init__(self):
            self.turns = []

        def record(self, role, content):
            self.turns.append((role, content))

    async def run():
        writer = Writer()
        recorder = AssistantTurnRecorder(writer)
        recorder.push_frame = AsyncMock()
        recorder.queue_spoken_text("Finished")
        waiting = asyncio.create_task(recorder.wait_for_output_completion())
        await asyncio.sleep(0)
        await recorder.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await waiting
        assert writer.turns == [("assistant", "Finished")]
        await recorder.cleanup()

    asyncio.run(run())


def test_turn_writer_retries_without_blocking_producer():
    class Store:
        def __init__(self):
            self.attempts = 0

        def add_turn(self, *_args):
            self.attempts += 1
            if self.attempts < 3:
                raise RuntimeError("temporary outage")

    async def run():
        store = Store()
        writer = TurnWriter(store, "session")
        writer.start()
        writer.record("user", "hello")
        await writer.close()
        assert store.attempts == 3
        assert writer.failed is False

    asyncio.run(run())
