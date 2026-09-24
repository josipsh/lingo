# Lingo

WhatsApp voice bot with **ElevenLabs STT/TTS** and **OpenAI LLM** for natural voice conversations, powered by Pipecat. Also supports echo modes for testing WhatsApp Cloud API Calling + WebRTC infrastructure.

## What it does

1. Meta sends a `calls` webhook when someone dials your WhatsApp Business number
2. This service accepts the call over WebRTC (via Pipecat)
3. In **conversation mode** (default):
   - Caller audio → ElevenLabs STT → OpenAI LLM → ElevenLabs TTS → Response audio
   - LinGo keeps the conversation moving and selects up to two useful corrections
   - Completed turns and post-call learning memory are stored in PostgreSQL
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
#   - Storage: DATABASE_URL, LEARNER_ID_SECRET

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

## Testing Without WhatsApp Setup

### CLI Tool for Local Testing

Test the STT/LLM/TTS pipeline with audio files **without** needing WhatsApp or Meta infrastructure:

```bash
# Set API keys
export ELEVENLABS_API_KEY=your-key
export OPENAI_API_KEY=your-key

# Process an audio file
uv run lingo-cli input.mp3

# Save bot response to file
uv run lingo-cli input.mp3 -o response.wav

# Or pass keys directly
uv run lingo-cli input.mp3 \
  --elevenlabs-api-key=your-key \
  --openai-api-key=your-key \
  -o response.wav
```

**What it does:**
1. Reads your audio file (any format FFmpeg supports)
2. Transcribes it with ElevenLabs STT
3. Sends transcript to OpenAI LLM
4. Generates response audio with ElevenLabs TTS
5. Prints transcript and response text
6. Optionally saves response audio to file

**Requirements:**
- FFmpeg installed (`apt install ffmpeg` or `brew install ffmpeg`)
- ElevenLabs and OpenAI API keys
- No WhatsApp, tunnels, or webhooks needed

**Use cases:**
- Quick testing during development
- Validating API credentials
- Testing conversation logic with sample audio
- CI/CD integration tests
- Demos without WhatsApp setup

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
| `OPENAI_MODEL` | no | Model (default: `gpt-6-luna`) |
| `DATABASE_URL` | yes* | SQLAlchemy PostgreSQL URL (*conversation mode) |
| `LEARNER_ID_SECRET` | yes* | Secret for protected learner IDs (*conversation mode) |
| `HOST` / `PORT` | no | Bind address (default `0.0.0.0:7860`) |

## Bot Modes

### Conversation Mode (Production)
Real AI voice assistant using:
- **ElevenLabs STT**: Speech-to-text with low latency
- **OpenAI LLM**: configured `gpt-6-luna` model for conversation and post-call analysis
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
  policy.py   # Shared live tutoring policy
  models.py   # SQLAlchemy transcript and learning-memory models
  analysis.py # Post-call extraction and recurring-pattern updates
  echo.py     # Echo processors for testing
  config.py   # Unified configuration
```

Export the 15-case evaluation set for teacher review without calling external services:

```bash
uv run lingo-eval --output teacher-review.csv
```

Add `--run` to collect responses from the configured OpenAI model before export.

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
