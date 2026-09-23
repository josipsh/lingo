import uvicorn
from fastapi import FastAPI

from .config import Settings
from .provider import (
    SpeechConfig,
    SpeechProvider,
    TranscriptionConfig,
    TranscriptionProvider,
)
from .providers.elevenlabs import ElevenLabsProvider, ElevenLabsSpeechProvider
from .routers.transcription import create_router


def create_app(
    provider: TranscriptionProvider,
    config: TranscriptionConfig,
    speech_provider: SpeechProvider,
    speech_config: SpeechConfig,
) -> FastAPI:
    app = FastAPI(title="Lingo realtime speech")
    app.include_router(create_router(provider, config, speech_provider, speech_config))
    return app


def application() -> FastAPI:
    settings = Settings.from_env()
    provider = ElevenLabsProvider(
        settings.elevenlabs_api_key, settings.elevenlabs_ws_url
    )
    speech_provider = ElevenLabsSpeechProvider(
        settings.elevenlabs_api_key,
        settings.elevenlabs_tts_url,
    )
    config = TranscriptionConfig(
        model=settings.elevenlabs_model,
        language=settings.transcription_language,
        audio_format=settings.audio_format,
    )
    speech_config = SpeechConfig(
        model=settings.elevenlabs_speech_model,
        voice_id=settings.elevenlabs_voice_id,
        language=settings.transcription_language,
        output_format=settings.speech_output_format,
    )
    return create_app(provider, config, speech_provider, speech_config)


def run() -> None:
    uvicorn.run("lingo.main:application", factory=True, host="127.0.0.1", port=8000)
