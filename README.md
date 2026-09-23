# Lingo

Lingo streams 16 kHz mono PCM audio over a provider-neutral WebSocket. It
returns partial and final transcripts, then streams an MP3 reading of each
non-empty final transcript. ElevenLabs handles transcription and speech in
production.

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

The optional settings are `ELEVENLABS_MODEL`, `ELEVENLABS_SPEECH_MODEL`,
`TRANSCRIPTION_LANGUAGE`, `ELEVENLABS_WS_URL`, and `ELEVENLABS_TTS_URL`.
Input audio is fixed to `pcm_16000`. Speech output is fixed to
`mp3_44100_128`.

The WebSocket has no authentication, rate limit, or generation budget. Run it
locally only. Add those controls before exposing it to untrusted clients, or
one connection can spend both transcription and speech credits. Use a
restricted ElevenLabs key with a credit quota.

## WebSocket contract

Connect to `ws://127.0.0.1:8000/ws/transcription`. Wait for `ready`, then send
base64-encoded signed 16-bit little-endian mono PCM at 16 kHz:

```json
{"type":"audio_chunk","audio":"<base64 PCM>"}
```

The server emits `ready`, `partial_transcript`, and `final_transcript` events.
A non-empty final transcript has a `clip_id`, followed by `speech_start`, one
or more `speech_chunk` events, and either `speech_end` or `speech_error` with
the same ID. Speech chunks contain base64-encoded `mp3_44100_128` bytes.
General `error` events report bad input or fatal transcription failures. The
server does not store audio, transcripts, generated speech, or session data.

## MP3 client

Install FFmpeg separately and make sure `ffmpeg` is in `PATH`. Then run:

```bash
uv run lingo-transcribe recording.mp3
```

Use `--url` to target a server at another address. The client converts the MP3
to the server's PCM format, streams it at its real audio rate, adds two seconds
of silence for voice activity detection, and waits for the final speech job.
It writes completed clips as `<clip_id>.mp3` in the current directory. If a
speech stream fails after sending bytes, it writes `<clip_id>.partial.mp3`.

## Tests

```bash
uv run pytest
```

Tests use in-memory providers and HTTP mock transports. They do not read the
API key, open external connections, or consume ElevenLabs credits.
