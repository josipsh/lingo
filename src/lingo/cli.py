import argparse
import asyncio
import base64
import binascii
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
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
    pending_clips: set[str] = field(default_factory=set)
    audio: dict[str, bytearray] = field(default_factory=dict)
    output_dir: Path = field(default_factory=Path.cwd)
    final_observed: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)


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
            transcript_state = TranscriptState()

            receiver = asyncio.create_task(
                _receive_events(
                    websocket,
                    source_audio_finished,
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

                transcript_state.changed.set()
                completion_waiter = asyncio.create_task(
                    _wait_for_completion(transcript_state, final_timeout)
                )
                try:
                    done, _ = await asyncio.wait(
                        {completion_waiter, receiver},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if receiver in done:
                        receiver.result()
                    completion_waiter.result()
                finally:
                    completion_waiter.cancel()
                    await asyncio.gather(completion_waiter, return_exceptions=True)
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
    transcript_state: TranscriptState,
) -> None:
    try:
        async for raw_event in websocket:
            event = json.loads(raw_event)
            event_type = event.get("type")
            if event_type != "speech_chunk":
                print(json.dumps(event), flush=True)
            if event_type in {"partial_transcript", "final_transcript"}:
                transcript_state.latest_type = event_type
            if event_type == "partial_transcript":
                if source_audio_finished.is_set():
                    transcript_state.final_observed = False
                transcript_state.changed.set()
            if event_type == "final_transcript":
                clip_id = event.get("clip_id")
                if clip_id is not None:
                    transcript_state.pending_clips.add(_valid_clip_id(clip_id))
                if source_audio_finished.is_set():
                    transcript_state.final_observed = True
                transcript_state.changed.set()
            elif event_type == "speech_start":
                clip_id = _valid_clip_id(event.get("clip_id"))
                transcript_state.audio.setdefault(clip_id, bytearray())
            elif event_type == "speech_chunk":
                clip_id = _valid_clip_id(event.get("clip_id"))
                encoded = event.get("audio")
                if not isinstance(encoded, str):
                    raise RuntimeError("Server returned an invalid speech chunk")
                try:
                    chunk = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise RuntimeError("Server returned an invalid speech chunk") from exc
                transcript_state.audio.setdefault(clip_id, bytearray()).extend(chunk)
            elif event_type == "speech_end":
                clip_id = _valid_clip_id(event.get("clip_id"))
                audio = transcript_state.audio.pop(clip_id, bytearray())
                (transcript_state.output_dir / f"{clip_id}.mp3").write_bytes(audio)
                transcript_state.pending_clips.discard(clip_id)
                transcript_state.changed.set()
            elif event_type == "speech_error":
                clip_id = _valid_clip_id(event.get("clip_id"))
                audio = transcript_state.audio.pop(clip_id, bytearray())
                if audio:
                    (transcript_state.output_dir / f"{clip_id}.partial.mp3").write_bytes(
                        audio
                    )
                transcript_state.pending_clips.discard(clip_id)
                print(
                    f"speech error for {clip_id}: "
                    f"{event.get('message', 'Speech generation unavailable')}",
                    file=sys.stderr,
                )
                transcript_state.changed.set()
            elif event_type == "error":
                raise RuntimeError(event.get("message", "Server error"))
        raise RuntimeError("Server closed before final speech completed")
    finally:
        _flush_partial_clips(transcript_state)


def _valid_clip_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise RuntimeError("Server returned an invalid clip identifier")
    return value


async def _wait_for_completion(state: TranscriptState, final_timeout: float) -> None:
    deadline = asyncio.get_running_loop().time() + final_timeout
    while True:
        state.changed.clear()
        if state.pending_clips:
            await state.changed.wait()
            continue
        if not state.final_observed:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for a final transcript")
            try:
                await asyncio.wait_for(state.changed.wait(), timeout=remaining)
            except TimeoutError as exc:
                raise TimeoutError("Timed out waiting for a final transcript") from exc
            continue
        return


def _flush_partial_clips(state: TranscriptState) -> None:
    for clip_id, audio in state.audio.items():
        if audio:
            (state.output_dir / f"{clip_id}.partial.mp3").write_bytes(audio)
    state.audio.clear()


def main() -> None:
    args = parser().parse_args()
    try:
        asyncio.run(transcribe(args.mp3, args.url, args.final_timeout))
    except (RuntimeError, TimeoutError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
