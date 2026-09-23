"""Per-call Pipecat pipeline for WhatsApp voice bot."""

from __future__ import annotations

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.services.elevenlabs import ElevenLabsSTTService, ElevenLabsTTSService
from pipecat.services.openai import OpenAILLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.whatsapp.api import WhatsAppConnectCall

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
    context_aggregator = None

    # Choose pipeline based on bot_mode
    if settings.bot_mode == "echo_live":
        processors.append(LiveEchoProcessor())
    elif settings.bot_mode == "echo_utterance":
        processors.extend(
            [
                VADProcessor(vad_analyzer=SileroVADAnalyzer()),
                UtteranceEchoProcessor(),
            ]
        )
    else:  # conversation mode
        # Real STT → LLM → TTS pipeline
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
                "content": "You are a helpful voice assistant on WhatsApp. Keep your responses concise and conversational since this is a voice call. Be friendly and natural.",
            },
        ]
        context = OpenAILLMContext(messages)
        context_aggregator = llm.create_context_aggregator(context)
        
        processors.extend(
            [
                stt,
                context_aggregator.user(),
                llm,
                tts,
                context_aggregator.assistant(),
            ]
        )

    processors.append(transport.output())

    pipeline = Pipeline(processors)
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=False,
        ),
    )
    runner = PipelineRunner()

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        logger.info("WebRTC connected for call_id={}", call_id)
        if context_aggregator:
            await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("WebRTC disconnected for call_id={}", call_id)
        await task.cancel()

    await runner.run(task)
    logger.info("Bot finished for call_id={}", call_id)
