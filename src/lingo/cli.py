"""Command-line tool for testing STT/LLM/TTS pipeline with audio files."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    AudioRawFrame,
    EndFrame,
    LLMMessagesFrame,
    TextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.elevenlabs import ElevenLabsSTTService, ElevenLabsTTSService
from pipecat.services.openai import OpenAILLMService

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

    async def run(self):
        """Read audio file and emit frames."""
        try:
            # Use FFmpeg to convert audio to PCM
            import subprocess
            import shutil

            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("FFmpeg is required but not found in PATH")

            logger.info(f"Reading audio from {self._audio_file}")

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

                audio_frame = AudioRawFrame(
                    audio=chunk,
                    sample_rate=self._sample_rate,
                    num_channels=1,
                )
                await self.push_frame(audio_frame)

            # Wait for process to complete
            _, stderr = process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg failed: {stderr.decode()}")

            logger.info("Finished reading audio file")
            await self.push_frame(EndFrame())

        except Exception as exc:
            logger.error(f"Error reading audio file: {exc}")
            raise


class TranscriptPrinter(FrameProcessor):
    """Print transcription and LLM responses."""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame):
            # This is transcribed text from STT
            print(f"\n[Transcript]: {frame.text}")
        elif isinstance(frame, LLMMessagesFrame):
            # This is the LLM response
            for message in frame.messages:
                if message.get("role") == "assistant":
                    content = message.get("content", "")
                    print(f"[Assistant]: {content}")

        await self.push_frame(frame, direction)


class AudioFileWriter(FrameProcessor):
    """Write output audio to file."""

    def __init__(self, output_file: Path, **kwargs):
        super().__init__(**kwargs)
        self._output_file = output_file
        self._audio_data = bytearray()
        self._sample_rate = 16000

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, AudioRawFrame):
            # Collect audio from TTS
            self._audio_data.extend(frame.audio)
        elif isinstance(frame, EndFrame):
            # Write collected audio to file
            if self._audio_data:
                await self._write_audio()

        await self.push_frame(frame, direction)

    async def _write_audio(self):
        """Write audio data to file using FFmpeg."""
        import subprocess
        import shutil

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

    # Create services
    stt = ElevenLabsSTTService(
        api_key=settings.elevenlabs_api_key,
        language="en",
    )

    llm = OpenAILLMService(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )

    tts = ElevenLabsTTSService(
        api_key=settings.elevenlabs_api_key,
        voice_id=settings.elevenlabs_voice_id,
    )

    # Set up conversation context
    messages = [
        {
            "role": "system",
            "content": "You are a helpful voice assistant. Keep your responses concise and conversational.",
        },
    ]
    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    # Build pipeline
    processors = [
        AudioFileReader(input_file),
        stt,
        TranscriptPrinter(),
        context_aggregator.user(),
        llm,
        tts,
        context_aggregator.assistant(),
    ]

    if output_file:
        processors.append(AudioFileWriter(output_file))

    pipeline = Pipeline(processors)
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=False,
            enable_metrics=False,
            enable_usage_metrics=False,
        ),
    )

    # Initialize context
    await task.queue_frames([context_aggregator.user().get_context_frame()])

    # Start file reader
    reader = processors[0]
    asyncio.create_task(reader.run())

    # Run pipeline
    runner = PipelineRunner()
    await runner.run(task)


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
        default="gpt-4o-mini",
        help="OpenAI model (default: gpt-4o-mini)",
    )
    parser.add_argument(
        "--elevenlabs-voice-id",
        default="21m00Tcm4TlvDq8ikWAM",
        help="ElevenLabs voice ID (default: Rachel)",
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
        logger.exception(f"Processing failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
