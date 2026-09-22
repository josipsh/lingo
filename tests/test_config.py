import pytest

from lingo.config import Settings


def test_settings_defaults() -> None:
    settings = Settings.from_mapping({"ELEVENLABS_API_KEY": "secret"})

    assert settings.elevenlabs_api_key == "secret"
    assert settings.elevenlabs_model == "scribe_v2_realtime"
    assert settings.transcription_language == "en"
    assert settings.audio_format == "pcm_16000"
    assert settings.elevenlabs_ws_url.startswith("wss://")


def test_api_key_is_required() -> None:
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY is required"):
        Settings.from_mapping({})


def test_settings_overrides() -> None:
    settings = Settings.from_mapping(
        {
            "ELEVENLABS_API_KEY": "secret",
            "ELEVENLABS_MODEL": "model",
            "TRANSCRIPTION_LANGUAGE": "hr",
            "ELEVENLABS_WS_URL": "ws://provider.test/realtime",
        }
    )

    assert settings.elevenlabs_model == "model"
    assert settings.transcription_language == "hr"
    assert settings.audio_format == "pcm_16000"
    assert settings.elevenlabs_ws_url == "ws://provider.test/realtime"


def test_unsupported_audio_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="AUDIO_FORMAT must be pcm_16000"):
        Settings.from_mapping(
            {"ELEVENLABS_API_KEY": "secret", "AUDIO_FORMAT": "pcm_8000"}
        )
