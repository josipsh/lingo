# Lingo

WhatsApp voice bot with **ElevenLabs STT/TTS** and **OpenAI LLM** for natural voice conversations, powered by Pipecat. Also supports echo modes for testing WhatsApp Cloud API Calling + WebRTC infrastructure.

## What it does

1. Meta sends a `calls` webhook when someone dials your WhatsApp Business number
2. This service accepts the call over WebRTC (via Pipecat)
3. In **conversation mode** (default):
   - Caller audio → ElevenLabs STT → OpenAI LLM → ElevenLabs TTS → Response audio
   - Natural voice conversation with AI assistant
4. In **echo modes** (for testing):
   - **echo_utterance**: buffers with Silero VAD, replays after silence
   - **echo_live**: immediate audio loopback

## Quick start (Docker)

```bash
cp .env.example .env
# Fill required credentials:
#   - WhatsApp: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, etc.
#   - ElevenLabs: ELEVENLABS_API_KEY
#   - OpenAI: OPENAI_API_KEY

docker compose up --build
```

Health check: `GET http://localhost:7860/health`  
Webhook: `https://<public-host>/whatsapp`

### Local tunnel

Meta needs a public HTTPS webhook. With the container on port 7860:

```bash
ngrok http 7860
# or: cloudflared tunnel --url http://localhost:7860
```

In Meta Developer Console → WhatsApp → Configuration → Webhooks:

- Callback URL: `https://<tunnel>/whatsapp`
- Verify token: same as `WHATSAPP_WEBHOOK_VERIFICATION_TOKEN`
- Subscribe field: **`calls`**

Also enable voice calling on the phone number (Calls tab). Register/connect the number if the Calls tab is missing.

## Quick start (uv, no Docker)

```bash
cp .env.example .env
uv sync
uv run lingo
```

## Environment

| Variable | Required | Description |
|---|---|---|
| `WHATSAPP_TOKEN` | yes | Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | yes | Business phone number ID |
| `WHATSAPP_WEBHOOK_VERIFICATION_TOKEN` | yes | Webhook verify token you choose |
| `WHATSAPP_APP_SECRET` | no | App secret; enables webhook signature checks |
| `BOT_MODE` | no | `conversation` (default), `echo_utterance`, or `echo_live` |
| `ELEVENLABS_API_KEY` | yes* | ElevenLabs API key (*required for conversation mode) |
| `ELEVENLABS_VOICE_ID` | no | Voice ID (default: Rachel) |
| `OPENAI_API_KEY` | yes* | OpenAI API key (*required for conversation mode) |
| `OPENAI_MODEL` | no | Model (default: gpt-4o-mini) |
| `HOST` / `PORT` | no | Bind address (default `0.0.0.0:7860`) |

## Bot Modes

### Conversation Mode (Production)
Real AI voice assistant using:
- **ElevenLabs STT**: Speech-to-text with low latency
- **OpenAI LLM**: GPT-4 or GPT-4o-mini for conversation
- **ElevenLabs TTS**: Natural voice synthesis

Set `BOT_MODE=conversation` and provide API keys.

### Echo Modes (Testing)
For validating WhatsApp infrastructure without AI costs:
- `echo_utterance`: Replays what you said after detecting silence
- `echo_live`: Immediate audio loopback

## Project layout

```
src/lingo/
  server.py   # FastAPI webhooks + health
  bot.py      # Per-call Pipecat pipeline (STT/LLM/TTS or echo)
  echo.py     # Echo processors for testing
  config.py   # Unified configuration
```

## Deploy notes

- Prefer the **Docker image** on a long-running host (Fly.io, Railway, Render, ECS, a VM). WhatsApp Calling needs persistent WebRTC connections; serverless platforms like **Vercel are a poor fit** even if the FastAPI entrypoint builds.
- Same image runs locally and in production; inject secrets via env
- Webhook path stays `/whatsapp`
- Media is WebRTC to Meta (not through the tunnel); the tunnel only carries HTTPS signaling
- Inbound (user-initiated) WhatsApp calls are free on Meta's Calling API

## Test call

1. Start the service and tunnel
2. Verify the webhook in Meta
3. From WhatsApp, call your business / test number
4. **Conversation mode**: Talk to the AI assistant naturally
5. **Echo mode**: Speak, pause — you should hear yourself replayed

## Architecture

This integrates two approaches:
- **WhatsApp WebRTC infrastructure** from PR #2 (echo bot)
- **ElevenLabs STT/TTS** capability from PR #3
- Unified via Pipecat's native service processors for optimal latency
