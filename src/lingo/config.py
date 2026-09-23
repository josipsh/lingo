"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv(override=False)


@dataclass(frozen=True)
class Settings:
    whatsapp_token: str
    whatsapp_phone_number_id: str
    whatsapp_webhook_verification_token: str
    whatsapp_app_secret: str | None
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    openai_api_key: str
    openai_model: str
    bot_mode: str
    host: str
    port: int

    @classmethod
    def from_env(cls, *, require_whatsapp: bool = True, require_ai: bool = True) -> Settings:
        token = os.getenv("WHATSAPP_TOKEN", "").strip()
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        verify_token = os.getenv("WHATSAPP_WEBHOOK_VERIFICATION_TOKEN", "").strip()
        app_secret = os.getenv("WHATSAPP_APP_SECRET", "").strip() or None
        
        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        elevenlabs_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM").strip()
        openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
        
        bot_mode = os.getenv("BOT_MODE", "conversation").strip().lower()
        host = os.getenv("HOST", "0.0.0.0").strip()
        port = int(os.getenv("PORT", "7860"))

        if bot_mode not in {"conversation", "echo_utterance", "echo_live"}:
            raise ValueError("BOT_MODE must be 'conversation', 'echo_utterance', or 'echo_live'")

        if require_whatsapp:
            missing = [
                name
                for name, value in [
                    ("WHATSAPP_TOKEN", token),
                    ("WHATSAPP_PHONE_NUMBER_ID", phone_number_id),
                    ("WHATSAPP_WEBHOOK_VERIFICATION_TOKEN", verify_token),
                ]
                if not value
            ]
            if missing:
                raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        if require_ai:
            missing = [
                name
                for name, value in [
                    ("ELEVENLABS_API_KEY", elevenlabs_api_key),
                    ("OPENAI_API_KEY", openai_api_key),
                ]
                if not value
            ]
            if missing:
                raise ValueError(f"Missing required environment variables for AI features: {', '.join(missing)}")

        return cls(
            whatsapp_token=token,
            whatsapp_phone_number_id=phone_number_id,
            whatsapp_webhook_verification_token=verify_token,
            whatsapp_app_secret=app_secret,
            elevenlabs_api_key=elevenlabs_api_key,
            elevenlabs_voice_id=elevenlabs_voice_id,
            openai_api_key=openai_api_key,
            openai_model=openai_model,
            bot_mode=bot_mode,
            host=host,
            port=port,
        )
