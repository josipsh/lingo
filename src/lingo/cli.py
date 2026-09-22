import argparse
import asyncio
import base64
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from websockets.asyncio.client import connect

SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2
CHUNK_DURATION = 0.2
CHUNK_SIZE = int(BYTES_PER_SECOND * CHUNK_DURATION)
TRAILING_SILENCE_SECONDS = 2.0


@dataclass
class TranscriptState:
    latest_type: str | None = None


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Stream an MP3 file to the Lingo transcription server"
    )
    command.add_argument("mp3", type=Path)
    command.add_argument(
        "--url",
        default="ws://127.0.0.1:8000/ws/transcription",
        help="server WebSocket URL",
    )
    command.add_argument(
        "--final-timeout",
        type=float,
        default=10.0,
        help="seconds to wait for a final transcript after streaming",
    )
    return command


async def transcribe(mp3: Path, url: str, final_timeout: float) -> None:
    if not mp3.is_file():
        raise RuntimeError(f"Audio file does not exist: {mp3}")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required but was not found in PATH")

    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(mp3),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None

    try:
        async with connect(url) as websocket:
            ready = json.loads(await websocket.recv())
            print(json.dumps(ready), flush=True)
            if ready.get("type") != "ready":
                raise RuntimeError("Server did not provide a ready event")

            source_audio_finished = asyncio.Event()
            final_after_source = asyncio.Event()
            transcript_state = TranscriptState()

            receiver = asyncio.create_task(
                _receive_events(
                    websocket,
                    source_audio_finished,
                    final_after_source,
                    transcript_state,
                )
            )
            try:
                while chunk := await process.stdout.read(CHUNK_SIZE):
                    await _send_chunk(websocket, chunk)
                    await asyncio.sleep(len(chunk) / BYTES_PER_SECOND)

                source_audio_finished.set()
                silence_chunks = round(TRAILING_SILENCE_SECONDS / CHUNK_DURATION)
                for _ in range(silence_chunks):
                    await _send_chunk(websocket, bytes(CHUNK_SIZE))
                    await asyncio.sleep(CHUNK_DURATION)

                if transcript_state.latest_type == "final_transcript":
                    final_after_source.set()
                final_waiter = asyncio.create_task(final_after_source.wait())
                done, pending = await asyncio.wait(
                    {final_waiter, receiver},
                    timeout=final_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    if task is not receiver:
                        task.cancel()
                if not done:
                    raise TimeoutError("Timed out waiting for a final transcript")
                if final_waiter not in done and receiver in done:
                    receiver.result()
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
    finally:
        if process.returncode is None:
            process.terminate()
        _, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {stderr.decode().strip()}")


async def _send_chunk(websocket, chunk: bytes) -> None:
    await websocket.send(
        json.dumps(
            {
                "type": "audio_chunk",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }
        )
    )


async def _receive_events(
    websocket,
    source_audio_finished: asyncio.Event,
    final_after_source: asyncio.Event,
    transcript_state: TranscriptState,
) -> None:
    async for raw_event in websocket:
        event = json.loads(raw_event)
        print(json.dumps(event), flush=True)
        event_type = event.get("type")
        if event_type in {"partial_transcript", "final_transcript"}:
            transcript_state.latest_type = event_type
        if event_type == "final_transcript":
            if source_audio_finished.is_set():
                final_after_source.set()
        elif event_type == "error":
            raise RuntimeError(event.get("message", "Server error"))
    raise RuntimeError("Server closed before the final transcript")


def main() -> None:
    args = parser().parse_args()
    try:
        asyncio.run(transcribe(args.mp3, args.url, args.final_timeout))
    except (RuntimeError, TimeoutError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
