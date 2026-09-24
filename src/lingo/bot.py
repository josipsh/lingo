"""Per-call Pipecat pipeline for WhatsApp voice bot."""

from __future__ import annotations

import asyncio

import aiohttp
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.elevenlabs.stt import ElevenLabsSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.whatsapp.api import WhatsAppConnectCall
from pipecat.workers.runner import WorkerRunner

from lingo.analysis import AnalysisClient
from lingo.config import Settings
from lingo.context import BoundedLLMContext
from lingo.database import SessionStore
from lingo.echo import LiveEchoProcessor, UtteranceEchoProcessor
from lingo.identity import learner_id
from lingo.policy import FALLBACK_MESSAGE, GREETING, PROMPT_VERSION, build_tutor_policy
from lingo.processors import (
    AssistantTurnRecorder,
    LLMFailureHandler,
    PipelineFailureTracker,
    TurnWriter,
    UserTurnRecorder,
)


async def run_bot(
    webrtc_connection: SmallWebRTCConnection,
    settings: Settings,
    call: WhatsAppConnectCall | None = None,
    store: SessionStore | None = None,
    analysis_client: AnalysisClient | None = None,
) -> None:
    """Answer one WhatsApp call with STT/LLM/TTS or echo mode."""

    call_id = call.id if call else None
    logger.info(
        "Starting bot mode={} call_id={}",
        settings.bot_mode,
        call_id,
    )

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )

    failure_tracker = PipelineFailureTracker()
    processors: list = [failure_tracker, transport.input()]
    assistant_aggregator = None
    aiohttp_session = None
    session_id = None
    failure_handler = None
    turn_writer = None
    assistant_recorder = None
    fallback_timeout_task = None
    run_failed = False

    try:
        if settings.bot_mode == "echo_live":
            processors.append(LiveEchoProcessor())
        elif settings.bot_mode == "echo_utterance":
            processors.extend(
                [
                    VADProcessor(vad_analyzer=SileroVADAnalyzer()),
                    UtteranceEchoProcessor(),
                ]
            )
        else:
            memory = None
            recent_summary = None
            if call:
                if not store:
                    logger.error("Persistence is unavailable for call_id={}", call_id)
                    await webrtc_connection.disconnect()
                    return
                protected_id = learner_id(call.from_, settings.learner_id_secret)
                try:
                    session_id = await asyncio.to_thread(
                        store.start_call,
                        protected_id,
                        call.id,
                        PROMPT_VERSION,
                        settings.openai_model,
                    )
                except Exception:
                    logger.exception("Could not start persistence for call_id={}", call_id)
                    await webrtc_connection.disconnect()
                    return
                try:
                    memory, recent_summary = await asyncio.to_thread(
                        store.prompt_memory, protected_id
                    )
                except Exception:
                    logger.exception("Could not load learning memory for call_id={}", call_id)
            turn_writer = TurnWriter(store, session_id)
            turn_writer.start()
            aiohttp_session = aiohttp.ClientSession()
            stt = ElevenLabsSTTService(
                api_key=settings.elevenlabs_api_key,
                aiohttp_session=aiohttp_session,
                settings=ElevenLabsSTTService.Settings(language="en"),
            )
            llm = OpenAILLMService(
                api_key=settings.openai_api_key,
                settings=OpenAILLMService.Settings(
                    model=settings.openai_model,
                    system_instruction=build_tutor_policy(
                        memory=memory, recent_summary=recent_summary
                    ),
                ),
            )
            tts = ElevenLabsTTSService(
                api_key=settings.elevenlabs_api_key,
                settings=ElevenLabsTTSService.Settings(voice=settings.elevenlabs_voice_id),
            )
            context_aggregator = LLMContextAggregatorPair(BoundedLLMContext())
            assistant_aggregator = context_aggregator.assistant()

            async def on_spoken(response: str) -> None:
                nonlocal fallback_timeout_task
                if response == FALLBACK_MESSAGE:
                    if fallback_timeout_task:
                        fallback_timeout_task.cancel()
                    await webrtc_connection.disconnect()

            assistant_recorder = AssistantTurnRecorder(turn_writer, on_spoken)

            async def fallback_timeout() -> None:
                await asyncio.sleep(15)
                await webrtc_connection.disconnect()

            async def handle_llm_failure() -> None:
                nonlocal fallback_timeout_task
                assistant_recorder.discard_generation()
                assistant_recorder.queue_spoken_text(FALLBACK_MESSAGE)
                fallback_timeout_task = asyncio.create_task(fallback_timeout())

            failure_handler = LLMFailureHandler(FALLBACK_MESSAGE, handle_llm_failure, llm)
            processors.extend(
                [
                    VADProcessor(vad_analyzer=SileroVADAnalyzer()),
                    stt,
                    UserTurnRecorder(turn_writer),
                    context_aggregator.user(),
                    failure_handler,
                    llm,
                    assistant_recorder,
                    tts,
                ]
            )

        processors.append(transport.output())
        if assistant_aggregator:
            processors.append(assistant_aggregator)

        worker = PipelineWorker(
            Pipeline(processors),
            params=PipelineParams(
                enable_metrics=True,
                enable_usage_metrics=False,
            ),
        )
        runner = WorkerRunner(handle_sigint=False)

        @transport.event_handler("on_client_connected")
        async def on_client_connected(_transport, _client):
            logger.info("WebRTC connected for call_id={}", call_id)
            if settings.bot_mode == "conversation":
                assistant_recorder.queue_spoken_text(GREETING)
                await worker.queue_frame(TTSSpeakFrame(text=GREETING))

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport, _client):
            logger.info("WebRTC disconnected for call_id={}", call_id)
            if assistant_recorder:
                await assistant_recorder.wait_for_output_completion()
            await worker.cancel(reason="WebRTC client disconnected")

        await runner.add_workers(worker)
        await runner.run()
        logger.info("Bot finished for call_id={}", call_id)
    except Exception:
        run_failed = True
        raise
    finally:
        if fallback_timeout_task:
            fallback_timeout_task.cancel()
        if aiohttp_session:
            await aiohttp_session.close()
        if store and session_id:
            if turn_writer:
                await turn_writer.close()
            failed = bool(
                run_failed
                or failure_tracker.failed
                or (failure_handler and failure_handler.failed)
                or (turn_writer and turn_writer.failed)
            )
            try:
                await asyncio.to_thread(store.finish_call, session_id, failed=failed)
            except Exception:
                logger.exception("Could not finalize persistence for call_id={}", call_id)
            if analysis_client and not (turn_writer and turn_writer.failed):
                try:
                    transcript = await asyncio.to_thread(store.transcript, session_id)
                    analysis = await analysis_client.analyze(transcript)
                    await asyncio.to_thread(store.save_analysis, session_id, analysis)
                except Exception:
                    logger.exception("Post-call analysis failed for call_id={}", call_id)
                    try:
                        await asyncio.to_thread(store.finish_call, session_id, failed=True)
                    except Exception:
                        logger.exception("Could not mark analysis failure for call_id={}", call_id)
            elif turn_writer and turn_writer.failed:
                logger.error(
                    "Skipping analysis because transcript persistence failed call_id={}",
                    call_id,
                )
