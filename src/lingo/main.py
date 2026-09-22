import uvicorn
from fastapi import FastAPI

from .config import Settings
from .provider import TranscriptionConfig, TranscriptionProvider
from .providers.elevenlabs import ElevenLabsProvider
from .routers.transcription import create_router


def create_app(
    provider: TranscriptionProvider, config: TranscriptionConfig
) -> FastAPI:
    app = FastAPI(title="Lingo realtime transcription")
    app.include_router(create_router(provider, config))
    return app


def application() -> FastAPI:
    settings = Settings.from_env()
    provider = ElevenLabsProvider(
        settings.elevenlabs_api_key, settings.elevenlabs_ws_url
    )
    config = TranscriptionConfig(
        model=settings.elevenlabs_model,
        language=settings.transcription_language,
        audio_format=settings.audio_format,
    )
    return create_app(provider, config)


def run() -> None:
    uvicorn.run("lingo.main:application", factory=True, host="127.0.0.1", port=8000)
