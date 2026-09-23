"""Command-line tool for testing STT/LLM/TTS pipeline with audio files."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp
from loguru import logger
from pipecat.frames.frames import (
    AudioRawFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.elevenlabs.stt import ElevenLabsSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.workers.runner import WorkerRunner

from lingo.config import Settings


class AudioFileReader(FrameProcessor):
    """Read audio from file and emit frames."""

    def __init__(self, audio_file: Path, **kwargs):
        super().__init__(**kwargs)
        self._audio_file = audio_file
        self._sample_rate = 16000

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        
        if isinstance(frame, AudioRawFrame):
            # Pass through audio frames
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    async def run(self, queue_frame: Callable[[Frame], Awaitable[None]]) -> None:
        """Read audio file and emit frames."""
        try:
            # Use FFmpeg to convert audio to PCM
            import shutil
            import subprocess

            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("FFmpeg is required but not found in PATH")

            logger.info(f"Reading audio from {self._audio_file}")
            await queue_frame(VADUserStartedSpeakingFrame(start_secs=0.2))

            process = subprocess.Popen(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel", "error",
                    "-i", str(self._audio_file),
                    "-f", "s16le",
                    "-acodec", "pcm_s16le",
                    "-ar", str(self._sample_rate),
                    "-ac", "1",
                    "pipe:1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            chunk_size = 4096
            while True:
                chunk = process.stdout.read(chunk_size)
                if not chunk:
                    break

                audio_frame = InputAudioRawFrame(
                    audio=chunk,
                    sample_rate=self._sample_rate,
                    num_channels=1,
                )
                await queue_frame(audio_frame)

            # Wait for process to complete
            _, stderr = process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: {stderr.decode()}")

            logger.info("Finished reading audio file")
            await queue_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2))

        except Exception as exc:
            logger.error(f"Error reading audio file: {exc}")
            raise


class TranscriptPrinter(FrameProcessor):
    """Print the input transcription."""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            print(f"\n[Transcript]: {frame.text}")

        await self.push_frame(frame, direction)


class AssistantPrinter(FrameProcessor):
    """Collect streamed LLM text and print the complete response."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._parts: list[str] = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            self._parts.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame) and self._parts:
            print(f"[Assistant]: {''.join(self._parts)}")
            self._parts.clear()

        await self.push_frame(frame, direction)


class ResponseCompletionTracker(FrameProcessor):
    """Signal when the generated speech response has finished."""

    def __init__(self, event: asyncio.Event, *, wait_for_tts: bool, **kwargs):
        super().__init__(**kwargs)
        self._event = event
        self._wait_for_tts = wait_for_tts
        self._llm_complete = False
        self._tts_complete = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseEndFrame):
            self._llm_complete = True
        elif isinstance(frame, TTSStoppedFrame):
            self._tts_complete = True

        if self._llm_complete and (not self._wait_for_tts or self._tts_complete):
            self._event.set()

        await self.push_frame(frame, direction)


class AudioFileWriter(FrameProcessor):
    """Write output audio to file."""

    def __init__(self, output_file: Path, **kwargs):
        super().__init__(**kwargs)
        self._output_file = output_file
        self._audio_data = bytearray()
        self._sample_rate = 16000

    @property
    def has_audio(self) -> bool:
        return bool(self._audio_data)

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSAudioRawFrame):
            # Collect audio from TTS
            self._audio_data.extend(frame.audio)
            self._sample_rate = frame.sample_rate
        elif isinstance(frame, EndFrame):
            # Write collected audio to file
            if self._audio_data:
                await self._write_audio()

        await self.push_frame(frame, direction)

    async def _write_audio(self):
        """Write audio data to file using FFmpeg."""
        import shutil
        import subprocess

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("FFmpeg not found, cannot save output audio")
            return

        logger.info(f"Writing {len(self._audio_data)} bytes to {self._output_file}")

        process = subprocess.Popen(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-f", "s16le",
                "-ar", str(self._sample_rate),
                "-ac", "1",
                "-i", "pipe:0",
                "-y",  # Overwrite output file
                str(self._output_file),
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        _, stderr = process.communicate(input=bytes(self._audio_data))
        if process.returncode != 0:
            logger.error(f"FFmpeg failed: {stderr.decode()}")
        else:
            logger.info(f"Saved output audio to {self._output_file}")


async def process_audio_file(
    input_file: Path,
    output_file: Path | None,
    settings: Settings,
) -> None:
    """Process audio file through STT → LLM → TTS pipeline."""
    async with aiohttp.ClientSession() as aiohttp_session:
        stt = ElevenLabsSTTService(
            api_key=settings.elevenlabs_api_key,
            aiohttp_session=aiohttp_session,
            settings=ElevenLabsSTTService.Settings(language="en"),
        )
        llm = OpenAILLMService(
            api_key=settings.openai_api_key,
            settings=OpenAILLMService.Settings(
                model=settings.openai_model,
                system_instruction=(
                    "You are a helpful voice assistant. Keep your responses concise and "
                    "conversational."
                ),
            ),
        )
        context_aggregator = LLMContextAggregatorPair(
            LLMContext(),
        )

        reader = AudioFileReader(input_file)
        response_complete = asyncio.Event()
        writer = None
        processors = [
            reader,
            stt,
            TranscriptPrinter(),
            context_aggregator.user(),
            llm,
            AssistantPrinter(),
        ]
        if output_file:
            writer = AudioFileWriter(output_file)
            tts = ElevenLabsHttpTTSService(
                api_key=settings.elevenlabs_api_key,
                aiohttp_session=aiohttp_session,
                settings=ElevenLabsHttpTTSService.Settings(
                    voice=settings.elevenlabs_voice_id
                ),
            )
            processors.extend(
                [
                    tts,
                    ResponseCompletionTracker(response_complete, wait_for_tts=True),
                    writer,
                ]
            )
        else:
            processors.append(
                ResponseCompletionTracker(response_complete, wait_for_tts=False)
            )
        processors.append(context_aggregator.assistant())

        worker = PipelineWorker(
            Pipeline(processors),
            params=PipelineParams(
                enable_metrics=False,
                enable_usage_metrics=False,
            ),
        )
        runner = WorkerRunner(handle_sigint=False)

        @runner.event_handler("on_ready")
        async def on_ready(_runner):
            await reader.run(worker.queue_frame)
            try:
                await asyncio.wait_for(response_complete.wait(), timeout=60)
            except TimeoutError:
                await worker.cancel(reason="Timed out waiting for the generated response")
                raise RuntimeError("Timed out waiting for the generated response") from None
            await worker.queue_frame(EndFrame())

        await runner.add_workers(worker)
        await runner.run()

        if writer and not writer.has_audio:
            raise RuntimeError(
                "No response audio was generated; check the ElevenLabs TTS error above "
                "and verify that your account can use ELEVENLABS_VOICE_ID"
            )


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test Lingo STT/LLM/TTS pipeline with audio files"
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input audio file (any format FFmpeg supports)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output audio file for bot response (optional)",
    )
    parser.add_argument(
        "--elevenlabs-api-key",
        help="ElevenLabs API key (or set ELEVENLABS_API_KEY env)",
    )
    parser.add_argument(
        "--openai-api-key",
        help="OpenAI API key (or set OPENAI_API_KEY env)",
    )
    parser.add_argument(
        "--openai-model",
        help="OpenAI model (default: OPENAI_MODEL env or gpt-4o-mini)",
    )
    parser.add_argument(
        "--elevenlabs-voice-id",
        help="ElevenLabs voice ID (default: ELEVENLABS_VOICE_ID env or Rachel)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = create_parser().parse_args(argv)

    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if args.verbose else "INFO",
        format="<level>{message}</level>",
    )

    # Check input file
    if not args.input.exists():
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)

    # Load settings from environment, override with CLI args
    try:
        import os
        
        if args.elevenlabs_api_key:
            os.environ["ELEVENLABS_API_KEY"] = args.elevenlabs_api_key
        if args.openai_api_key:
            os.environ["OPENAI_API_KEY"] = args.openai_api_key
        if args.openai_model:
            os.environ["OPENAI_MODEL"] = args.openai_model
        if args.elevenlabs_voice_id:
            os.environ["ELEVENLABS_VOICE_ID"] = args.elevenlabs_voice_id

        settings = Settings.from_env(require_whatsapp=False, require_ai=True)
    except ValueError as exc:
        logger.error(f"Configuration error: {exc}")
        logger.info("Set ELEVENLABS_API_KEY and OPENAI_API_KEY environment variables")
        sys.exit(1)

    # Process audio file
    logger.info(f"Processing {args.input}")
    if args.output:
        logger.info(f"Will save response to {args.output}")

    try:
        asyncio.run(process_audio_file(args.input, args.output, settings))
        logger.success("Processing complete")
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        sys.exit(130)
    except Exception as exc:
        logger.error(f"Processing failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
