import asyncio
import base64
import binascii
import json
import logging
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..provider import (
    ProviderError,
    ProviderEvent,
    SpeechConfig,
    SpeechProvider,
    TranscriptionConfig,
    TranscriptionProvider,
    TranscriptionSession,
)

logger = logging.getLogger(__name__)


def create_router(
    provider: TranscriptionProvider,
    config: TranscriptionConfig,
    speech_provider: SpeechProvider,
    speech_config: SpeechConfig,
) -> APIRouter:
    router = APIRouter(prefix="/ws", tags=["transcription"])

    @router.websocket("/transcription")
    async def transcription(websocket: WebSocket) -> None:
        await websocket.accept()
        messages: asyncio.Queue[dict | None] = asyncio.Queue()
        speech_jobs: asyncio.Queue[SpeechJob] = asyncio.Queue()
        disconnected = asyncio.Event()
        send_lock = asyncio.Lock()

        async def send_json(message: dict) -> None:
            async with send_lock:
                await websocket.send_json(message)

        async def collect_messages() -> None:
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    await messages.put(message)
            finally:
                disconnected.set()
                await messages.put(None)

        collector = asyncio.create_task(collect_messages())
        connect_task = asyncio.create_task(provider.connect(config))
        disconnect_task = asyncio.create_task(disconnected.wait())
        session: TranscriptionSession | None = None

        try:
            done, _ = await asyncio.wait(
                {connect_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if connect_task not in done:
                connect_task.cancel()
                await asyncio.gather(connect_task, return_exceptions=True)
                return

            try:
                session = connect_task.result()
            except Exception:
                if not disconnected.is_set():
                    logger.exception("Could not create transcription provider session")
                    await _fatal_error(websocket)
                return

            if disconnected.is_set():
                return

            async def receive_audio() -> None:
                while True:
                    frame = await messages.get()
                    if frame is None:
                        raise WebSocketDisconnect()
                    try:
                        audio = _parse_frame(frame)
                    except ClientMessageError as exc:
                        await send_json(
                            {"type": "error", "code": 400, "message": str(exc)}
                        )
                        continue
                    await session.send_audio(audio)

            async def forward_events() -> None:
                async for event in session.events():
                    if event.type == "final" and event.text is not None:
                        text = event.text
                        if text.strip():
                            clip_id = uuid.uuid4().hex
                            await send_json(
                                {
                                    "type": "final_transcript",
                                    "clip_id": clip_id,
                                    "text": text,
                                }
                            )
                            await speech_jobs.put(
                                SpeechJob(clip_id, select_speech_text(text))
                            )
                        else:
                            await send_json(
                                {"type": "final_transcript", "text": text}
                            )
                    else:
                        await send_json(_client_event(event))
                raise ProviderError("Provider event stream ended unexpectedly")

            async def generate_speech() -> None:
                while True:
                    job = await speech_jobs.get()
                    await send_json(
                        {
                            "type": "speech_start",
                            "clip_id": job.clip_id,
                            "format": speech_config.output_format,
                        }
                    )
                    try:
                        async for chunk in speech_provider.synthesize(
                            job.text, speech_config
                        ):
                            if chunk:
                                await send_json(
                                    {
                                        "type": "speech_chunk",
                                        "clip_id": job.clip_id,
                                        "audio": base64.b64encode(chunk).decode("ascii"),
                                    }
                                )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.exception(
                            "Speech generation failed for clip %s (request_id=%s)",
                            job.clip_id,
                            getattr(exc, "request_id", None),
                        )
                        await send_json(
                            {
                                "type": "speech_error",
                                "clip_id": job.clip_id,
                                "code": 502,
                                "message": "Speech generation unavailable",
                            }
                        )
                    else:
                        await send_json(
                            {"type": "speech_end", "clip_id": job.clip_id}
                        )
                    finally:
                        speech_jobs.task_done()

            tasks = {
                asyncio.create_task(receive_audio()),
                asyncio.create_task(forward_events()),
                asyncio.create_task(generate_speech()),
                disconnect_task,
            }
            try:
                completed, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in completed:
                    task.result()
            except WebSocketDisconnect:
                pass
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Fatal transcription pipeline error")
                await _fatal_error(websocket)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            if not connect_task.done():
                connect_task.cancel()
            disconnect_task.cancel()
            collector.cancel()
            await asyncio.gather(
                connect_task, disconnect_task, collector, return_exceptions=True
            )
            if session is not None:
                try:
                    await session.close()
                except Exception:
                    logger.exception("Could not close transcription provider session")

    return router


class ClientMessageError(ValueError):
    pass


@dataclass(frozen=True)
class SpeechJob:
    clip_id: str
    text: str


def select_speech_text(transcript: str) -> str:
    return transcript


def _parse_frame(frame: dict) -> bytes:
    raw_message = frame.get("text")
    if not isinstance(raw_message, str):
        raise ClientMessageError("Message must be a text JSON message")
    return _parse_audio(raw_message)


def _parse_audio(raw_message: str) -> bytes:
    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError as exc:
        raise ClientMessageError("Message must be valid JSON") from exc
    if not isinstance(message, dict):
        raise ClientMessageError("Message must be a JSON object")
    if message.get("type") != "audio_chunk":
        raise ClientMessageError("Unsupported message type")
    encoded = message.get("audio")
    if not isinstance(encoded, str):
        raise ClientMessageError("Audio must be a base64 string")
    if not encoded:
        raise ClientMessageError("Audio must not be empty")
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ClientMessageError("Audio must be valid base64") from exc
    if not audio:
        raise ClientMessageError("Audio must not be empty")
    return audio


def _client_event(event: ProviderEvent) -> dict[str, str]:
    if event.type == "ready" and event.session_id:
        return {"type": "ready", "session_id": event.session_id}
    if event.type == "partial" and event.text is not None:
        return {"type": "partial_transcript", "text": event.text}
    raise ProviderError("Provider returned an invalid event")


async def _fatal_error(websocket: WebSocket) -> None:
    if websocket.client_state is WebSocketState.CONNECTED:
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": 502,
                    "message": "Transcription service unavailable",
                }
            )
            await websocket.close(code=1011)
        except (RuntimeError, WebSocketDisconnect):
            pass
