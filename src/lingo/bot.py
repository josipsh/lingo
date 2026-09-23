"""Per-call Pipecat pipeline for WhatsApp voice bot."""

from __future__ import annotations

import aiohttp
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
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

from lingo.config import Settings
from lingo.echo import LiveEchoProcessor, UtteranceEchoProcessor


async def run_bot(
    webrtc_connection: SmallWebRTCConnection,
    settings: Settings,
    call: WhatsAppConnectCall | None = None,
) -> None:
    """Answer one WhatsApp call with STT/LLM/TTS or echo mode."""

    caller = call.from_ if call else None
    call_id = call.id if call else None
    logger.info(
        "Starting bot mode={} call_id={} caller={}",
        settings.bot_mode,
        call_id,
        caller,
    )

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
        ),
    )

    processors: list = [transport.input()]
    assistant_aggregator = None
    aiohttp_session = None

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
                    system_instruction=(
                        "You are a helpful voice assistant on WhatsApp. Keep your responses "
                        "concise, conversational, friendly, and natural."
                    ),
                ),
            )
            tts = ElevenLabsTTSService(
                api_key=settings.elevenlabs_api_key,
                settings=ElevenLabsTTSService.Settings(voice=settings.elevenlabs_voice_id),
            )
            context_aggregator = LLMContextAggregatorPair(
                LLMContext(),
            )
            assistant_aggregator = context_aggregator.assistant()
            processors.extend(
                [
                    VADProcessor(vad_analyzer=SileroVADAnalyzer()),
                    stt,
                    context_aggregator.user(),
                    llm,
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

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(_transport, _client):
            logger.info("WebRTC disconnected for call_id={}", call_id)
            await worker.cancel(reason="WebRTC client disconnected")

        await runner.add_workers(worker)
        await runner.run()
        logger.info("Bot finished for call_id={}", call_id)
    finally:
        if aiohttp_session:
            await aiohttp_session.close()
