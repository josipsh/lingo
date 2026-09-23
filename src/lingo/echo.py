"""Audio echo processors used to mock STT / LLM / TTS."""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class LiveEchoProcessor(FrameProcessor):
    """Immediately play caller audio back on the output transport."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
            )

        await self.push_frame(frame, direction)


class UtteranceEchoProcessor(FrameProcessor):
    """Buffer speech while the caller talks, then replay it after they stop.

    This is a better WhatsApp integration mock than live echo: it feels like a
    bot turn without needing STT, LLM, or TTS providers.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._buffer = bytearray()
        self._sample_rate: int | None = None
        self._num_channels: int = 1
        self._recording = False
        self._replaying = False

    def _reset_buffer(self) -> None:
        self._buffer.clear()
        self._sample_rate = None
        self._num_channels = 1

    async def _replay(self) -> None:
        if not self._buffer or not self._sample_rate:
            self._reset_buffer()
            return

        audio = bytes(self._buffer)
        sample_rate = self._sample_rate
        num_channels = self._num_channels
        duration_secs = len(audio) / (2 * num_channels * sample_rate)
        logger.info(
            "Replaying {:.2f}s of caller audio ({} bytes @ {}Hz)",
            duration_secs,
            len(audio),
            sample_rate,
        )

        self._replaying = True
        self._reset_buffer()
        try:
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=audio,
                    sample_rate=sample_rate,
                    num_channels=num_channels,
                )
            )
        finally:
            self._replaying = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, VADUserStartedSpeakingFrame):
            if not self._replaying:
                self._recording = True
                self._reset_buffer()
                logger.debug("Caller started speaking; buffering audio")
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            if self._recording:
                self._recording = False
                logger.debug("Caller stopped speaking; replaying buffer")
                await self._replay()
        elif isinstance(frame, InputAudioRawFrame):
            if self._recording and not self._replaying:
                self._buffer.extend(frame.audio)
                self._sample_rate = frame.sample_rate
                self._num_channels = frame.num_channels

        await self.push_frame(frame, direction)
