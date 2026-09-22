# Lingo

Lingo streams 16 kHz mono PCM audio over a provider-neutral WebSocket and
returns partial and final transcripts. ElevenLabs Scribe handles realtime
transcription in production.

## Setup

Install the Python dependencies:

```bash
uv sync
```

Export the server-side API key. The server never sends it to clients.

```bash
export ELEVENLABS_API_KEY="..."
```

Start the server:

```bash
uv run lingo-server
```

The optional settings are `ELEVENLABS_MODEL`, `TRANSCRIPTION_LANGUAGE`, and
`ELEVENLABS_WS_URL`. Audio is fixed to `pcm_16000`; the server rejects any
other `AUDIO_FORMAT` value.

## WebSocket contract

Connect to `ws://127.0.0.1:8000/ws/transcription`. Wait for `ready`, then send
base64-encoded signed 16-bit little-endian mono PCM at 16 kHz:

```json
{"type":"audio_chunk","audio":"<base64 PCM>"}
```

The server emits `ready`, `partial_transcript`, `final_transcript`, and `error`
events. It does not store audio, transcripts, or session data.

## MP3 client

Install FFmpeg separately and make sure `ffmpeg` is in `PATH`. Then run:

```bash
uv run lingo-transcribe recording.mp3
```

Use `--url` to target a server at another address. The client converts the MP3
to the server's PCM format, streams it at its real audio rate, adds two seconds
of silence for voice activity detection, and waits for a final transcript.

## Tests

```bash
uv run pytest
```

Tests use an in-memory provider. They do not read the API key, open external
connections, or consume ElevenLabs credits.
