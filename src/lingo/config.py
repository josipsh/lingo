from collections.abc import Mapping
from dataclasses import dataclass
from os import environ


@dataclass(frozen=True)
class Settings:
    elevenlabs_api_key: str
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"
    elevenlabs_model: str = "scribe_v2_realtime"
    elevenlabs_speech_model: str = "eleven_flash_v2_5"
    transcription_language: str = "en"
    audio_format: str = "pcm_16000"
    speech_output_format: str = "mp3_44100_128"
    elevenlabs_ws_url: str = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
    elevenlabs_tts_url: str = "https://api.elevenlabs.io/v1/text-to-speech"

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings":
        api_key = values.get("ELEVENLABS_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is required")
        audio_format = values.get("AUDIO_FORMAT", cls.audio_format)
        if audio_format != "pcm_16000":
            raise ValueError("AUDIO_FORMAT must be pcm_16000")

        return cls(
            elevenlabs_api_key=api_key,
            elevenlabs_model=values.get("ELEVENLABS_MODEL", cls.elevenlabs_model),
            elevenlabs_speech_model=values.get(
                "ELEVENLABS_SPEECH_MODEL", cls.elevenlabs_speech_model
            ),
            transcription_language=values.get(
                "TRANSCRIPTION_LANGUAGE", cls.transcription_language
            ),
            audio_format=audio_format,
            elevenlabs_ws_url=values.get(
                "ELEVENLABS_WS_URL", cls.elevenlabs_ws_url
            ),
            elevenlabs_tts_url=values.get(
                "ELEVENLABS_TTS_URL", cls.elevenlabs_tts_url
            ),
        )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.from_mapping(environ)
