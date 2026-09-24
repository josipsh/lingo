"""Pipecat processors for completed turns and pipeline failures."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    ErrorFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from lingo.database import SessionStore


class TurnWriter:
    """Write turns in order without holding up the media pipeline."""

    def __init__(self, store: SessionStore | None, session_id: str | None):
        self._store = store
        self._session_id = session_id
        self._queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.failed = False

    def start(self) -> None:
        if self._store and self._session_id:
            self._task = asyncio.create_task(self._run())

    def record(self, role: str, content: str) -> None:
        if self._task:
            self._queue.put_nowait((role, content))

    async def close(self) -> None:
        if not self._task:
            return
        self._queue.put_nowait(None)
        await self._task

    async def _run(self) -> None:
        while item := await self._queue.get():
            for attempt in range(3):
                try:
                    await asyncio.to_thread(
                        self._store.add_turn, self._session_id, item[0], item[1]
                    )
                    break
                except Exception:
                    logger.exception(
                        "Turn persistence failed session_id={} role={} attempt={}",
                        self._session_id,
                        item[0],
                        attempt + 1,
                    )
                    if attempt < 2:
                        await asyncio.sleep(0.1 * (attempt + 1))
                    else:
                        self.failed = True


class UserTurnRecorder(FrameProcessor):
    def __init__(self, writer: TurnWriter, **kwargs):
        super().__init__(**kwargs)
        self._writer = writer

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._writer.record("user", frame.text)
        await self.push_frame(frame, direction)


class AssistantTurnRecorder(FrameProcessor):
    def __init__(
        self,
        writer: TurnWriter,
        on_spoken: Callable[[str], Awaitable[None]] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._writer = writer
        self._on_spoken = on_spoken
        self._parts: list[str] = []
        self._pending: deque[str] = deque()
        self._ignore_response_end = False
        self._output_complete = asyncio.Event()
        self._output_complete.set()

    def queue_spoken_text(self, text: str) -> None:
        self._output_complete.clear()
        self._pending.append(text)

    def discard_generation(self) -> None:
        self._parts.clear()
        self._ignore_response_end = True

    async def wait_for_output_completion(self, timeout: float = 0.5) -> None:
        if not self._pending:
            return
        try:
            await asyncio.wait_for(self._output_complete.wait(), timeout=timeout)
        except TimeoutError:
            pass

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self._parts.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            response = "".join(self._parts).strip()
            self._parts.clear()
            if self._ignore_response_end:
                self._ignore_response_end = False
            elif response:
                self._output_complete.clear()
                self._pending.append(response)
        elif isinstance(frame, InterruptionFrame):
            self._parts.clear()
            self._pending.clear()
            self._output_complete.set()
        elif isinstance(frame, BotStoppedSpeakingFrame) and self._pending:
            response = self._pending.popleft()
            self._writer.record("assistant", response)
            if not self._pending:
                self._output_complete.set()
            if self._on_spoken:
                await self._on_spoken(response)
        await self.push_frame(frame, direction)


class PipelineFailureTracker(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.failed = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, ErrorFrame):
            self.failed = True
        await self.push_frame(frame, direction)


class LLMFailureHandler(FrameProcessor):
    def __init__(
        self,
        fallback_message: str,
        on_failure: Callable[[], Awaitable[None]],
        expected_processor: FrameProcessor,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._fallback_message = fallback_message
        self._on_failure = on_failure
        self._expected_processor = expected_processor
        self.failed = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, ErrorFrame)
            and frame.processor is self._expected_processor
            and not self.failed
        ):
            self.failed = True
            await self.push_frame(
                TTSSpeakFrame(text=self._fallback_message), FrameDirection.DOWNSTREAM
            )
            await self._on_failure()
            return
        await self.push_frame(frame, direction)
