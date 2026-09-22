# Realtime Speech-to-Text Prototype

## Problem Statement

People who practice a language while walking, commuting, exercising, or doing everyday activities need a voice-first experience that does not require continuous screen interaction. The backend must provide the realtime transcription foundation for that experience by accepting streamed audio over a WebSocket and returning transcripts as speech is processed.

The first client integration is expected to originate from WhatsApp, but its final media-delivery behavior is not yet defined. The initial prototype therefore needs a small, provider-neutral WebSocket contract that is easy to exercise from a command-line client and can later sit behind a WhatsApp-specific adapter. It must not retain audio or transcripts.

## Solution

Build a FastAPI WebSocket endpoint that accepts base64-encoded audio chunks, streams decoded audio bytes to ElevenLabs Realtime Speech-to-Text, and returns partial and committed transcripts as JSON messages.

The prototype will use 16-bit little-endian mono PCM at 16 kHz. A command-line client will accept an MP3 file, use an externally installed FFmpeg executable to convert it to the required PCM stream, send the stream in realtime-sized chunks, and print server events. ElevenLabs voice activity detection will determine utterance boundaries and commit stable transcripts while the client remains connected.

The application will isolate the transcription provider behind a small dependency boundary. Production will use a direct WebSocket connection to ElevenLabs. Unit tests will construct the real FastAPI application with a test-only in-memory fake provider, allowing the public WebSocket behavior to be tested without network calls, paid API usage, or monkeypatching.

## User Stories

1. As a language learner, I want my speech transcribed while I speak, so that I can practice without interacting with a screen.
2. As a language learner, I want to receive provisional text quickly, so that the experience feels responsive.
3. As a language learner, I want to receive a stable transcript after I pause, so that later language-practice features can act on a completed utterance.
4. As a client developer, I want to stream audio incrementally over one WebSocket, so that long-running voice sessions do not require uploading complete recordings.
5. As a client developer, I want a provider-neutral input contract, so that the client is not coupled to ElevenLabs field names.
6. As a client developer, I want distinct partial and final transcript event types, so that I can replace provisional text without confusing it with committed text.
7. As a client developer, I want a ready event, so that I know the upstream transcription session is available before relying on transcript output.
8. As a client developer, I want the ready event to include the upstream session identifier, so that a session can be correlated during prototype debugging.
9. As a client developer, I want malformed input to produce a structured error without ending the session, so that one bad message does not interrupt an otherwise valid stream.
10. As a client developer, I want fatal upstream failures to produce a structured error before disconnection, so that I can distinguish provider failure from an unexplained network loss.
11. As a client developer, I want error messages to contain numeric HTTP-like codes and high-level descriptions, so that failures can be handled consistently without exposing internal details.
12. As a client developer, I want the connection lifetime to define the transcription-session lifetime, so that session cleanup is predictable.
13. As a client developer, I want audio to remain ephemeral, so that raw voice data is not retained by this prototype.
14. As a client developer, I want transcripts to remain ephemeral, so that the prototype does not create an undeclared conversation history.
15. As a WhatsApp integration developer, I want audio transport and provider details isolated from the core WebSocket contract, so that a future WhatsApp adapter can be added without redesigning transcription.
16. As a developer, I want to stream an MP3 file from a CLI, so that I can manually verify the complete transcription flow before a product client exists.
17. As a developer, I want the CLI to convert MP3 audio to the required PCM format, so that test input is easy to create.
18. As a developer, I want the CLI to clearly report a missing FFmpeg installation, so that local setup failures are easy to diagnose.
19. As a developer, I want English to be the initial configured language, so that the first implementation has deterministic transcription behavior.
20. As a developer, I want settings sourced from environment variables into a standard-library dataclass, so that deployment configuration does not depend on an application-level settings framework.
21. As a developer, I want the ElevenLabs API key to remain server-side, so that clients never receive provider credentials.
22. As a developer, I want unit tests to use a fake transcription provider without monkeypatching, so that tests are deterministic and explicit about dependencies.
23. As a developer, I want tests to connect through the real FastAPI WebSocket endpoint, so that they validate externally observable behavior rather than implementation details.
24. As an operator, I want fatal ElevenLabs authentication, quota, and transport errors logged internally, so that production failures can be diagnosed.
25. As an operator, I want clients to receive only safe, high-level failure descriptions, so that provider details and secrets are not leaked.
26. As an operator, I want provider sessions closed when clients disconnect, so that abandoned connections do not continue consuming resources.
27. As an operator, I want no application-level audio chunk-size limit in this prototype, so that the backend does not impose a limit beyond the provider and framework constraints.

## Implementation Decisions

- The service will use FastAPI and expose one WebSocket endpoint at `/ws/transcription`.
- The endpoint will not require client authentication in this prototype.
- Each accepted client WebSocket corresponds to one ElevenLabs realtime transcription session.
- Production will connect directly to the ElevenLabs Realtime Speech-to-Text WebSocket protocol rather than use the official Python SDK. The SDK currently depends on Pydantic, which conflicts with the decision not to use Pydantic in application code.
- FastAPI has a transitive dependency on Pydantic. Application code will not import or directly use Pydantic for settings, payloads, or validation.
- The ElevenLabs model will default to `scribe_v2_realtime`.
- ElevenLabs will use voice activity detection as its commit strategy. The prototype will rely on provider defaults for VAD tuning.
- The transcription language will default to English and will be configurable through the environment for future changes.
- Input audio will be fixed to `pcm_16000`: signed 16-bit little-endian PCM, 16 kHz, mono.
- Audio format is an environment-level service setting rather than a per-session or per-message option. The initial implementation only needs to support `pcm_16000`.
- Configuration will be represented by a standard-library dataclass populated from `os.environ`.
- The ElevenLabs API key is required server configuration. Provider model, language, audio format, and provider WebSocket base URL may be represented as configuration with documented defaults where appropriate.
- The server will use standard-library JSON handling and explicit validation for incoming messages.
- The single accepted client input payload is:

  ```json
  {
    "type": "audio_chunk",
    "audio": "<base64-encoded PCM bytes>"
  }
  ```

- `type` must equal `audio_chunk` and `audio` must be a valid, non-empty base64 string. Unknown event types, malformed JSON, missing fields, incorrect field types, and invalid base64 are recoverable client-input errors.
- The server will not add an application-level decoded chunk-size limit. Provider, WebSocket server, and infrastructure limits still apply.
- Once ElevenLabs emits `session_started`, the server will send:

  ```json
  {
    "type": "ready",
    "session_id": "<ElevenLabs session identifier>"
  }
  ```

- Partial ElevenLabs transcripts will be normalized to:

  ```json
  {
    "type": "partial_transcript",
    "text": "<provisional transcription>"
  }
  ```

- Committed ElevenLabs transcripts will be normalized to:

  ```json
  {
    "type": "final_transcript",
    "text": "<stable transcription>"
  }
  ```

- Errors will be normalized to:

  ```json
  {
    "type": "error",
    "code": 400,
    "message": "<high-level description>"
  }
  ```

- Error `code` values are HTTP-like application codes carried inside JSON. They are not HTTP response statuses because the WebSocket handshake has already completed.
- Recoverable client-input errors will use an appropriate 4xx-like code, send an error event, and leave the client WebSocket connected.
- Fatal provider or internal pipeline errors will use an appropriate 5xx-like code, normally `502` for an upstream provider failure. The server will send the error event and then close the client WebSocket with WebSocket close code `1011`.
- Raw exception text, provider payload details, and credentials will not be sent to clients. Detailed errors will be logged server-side.
- A normal client disconnect ends the session. The backend will close the ElevenLabs connection immediately and will not attempt a final manual commit.
- Because the client is disconnected, no transcript can be delivered after disconnection. Clients must remain connected until VAD has produced the expected `final_transcript` event.
- The CLI will add or preserve enough trailing silence for VAD to commit, wait for a final transcript, and then disconnect.
- The CLI will accept an MP3 file and WebSocket URL, invoke FFmpeg as a subprocess, convert audio to signed 16-bit little-endian mono PCM at 16 kHz, split the output into realtime-sized chunks, base64-encode each chunk, and send `audio_chunk` messages at an audio-realistic cadence.
- FFmpeg is a documented external prerequisite for the CLI. It is not a server runtime dependency.
- The server will not persist audio, partial transcripts, final transcripts, or session metadata.
- Production code will define a small transcription-provider boundary and accept that dependency when constructing the FastAPI application. The production dependency will implement ElevenLabs connectivity.
- The fake provider will exist only in test code. No fake-provider mode, environment switch, or test behavior will ship in production code.
- Concurrent WebSocket connections will use independent provider sessions and in-memory state.

## Testing Decisions

- Tests will focus on externally observable behavior through the FastAPI WebSocket endpoint rather than private functions or provider implementation details.
- The highest and only new test seam will be the transcription-provider dependency supplied when the application is constructed.
- Unit tests will instantiate the real FastAPI application with a test-only in-memory fake provider. They will not use monkeypatching, a real ElevenLabs API key, external network calls, or paid provider usage.
- The critical-path test will connect to `/ws/transcription`, observe `ready`, send a valid base64 PCM `audio_chunk`, make the fake emit partial and committed transcript events, and assert the normalized `partial_transcript` and `final_transcript` responses.
- A cleanup test will verify that disconnecting the client closes the provider session without requesting a manual commit.
- Recoverable-validation tests will cover malformed JSON, unknown message types, missing audio, incorrect audio field types, invalid base64, and empty audio. Each case must return a structured 4xx-like error and leave the connection usable for a subsequent valid chunk.
- A fatal-provider test will make the fake report an upstream failure and assert a safe `502` error followed by WebSocket close code `1011`.
- A provider-connection test will verify that the configured model, English language, PCM format, and VAD strategy are passed to the production ElevenLabs boundary without making a live network call.
- Configuration tests will validate defaults and required environment values through direct construction inputs where possible, without modifying global environment state through monkeypatching.
- CLI tests will cover command validation and PCM message generation at a process boundary that does not require a real ElevenLabs connection. FFmpeg-dependent behavior may be verified separately where FFmpeg is available.
- The repository currently has no prior test suite or established testing conventions to reuse.
- Live ElevenLabs integration tests are not part of the required automated test suite.

## Out of Scope

- WhatsApp API integration, webhook handling, media download, and WhatsApp-specific audio adaptation.
- A browser, mobile, or graphical client.
- Authentication or authorization for the application WebSocket.
- User accounts, lessons, exercises, feedback, language coaching, conversation orchestration, or AI responses beyond transcription.
- Text-to-speech generation.
- Persisting audio, transcripts, sessions, or analytics.
- Resuming interrupted sessions or replaying missed transcript events.
- Manually committing an utterance when a client disconnects.
- Supporting MP3, Ogg/Opus, or other encoded media directly in the server endpoint.
- Supporting multiple input audio formats in the first implementation.
- Per-session language or audio-format selection.
- Word-level timestamps, language detection, entity detection, speaker diarization, or transcript redaction.
- Application-level audio chunk-size limits.
- ElevenLabs single-use client tokens because provider access remains server-side.
- Production deployment, horizontal scaling, shared connection state, rate limiting, quotas, billing controls, metrics, tracing, and alerting.
- A live-provider test in the default automated test suite.

## Further Notes

- WhatsApp does not currently define this backend's streaming contract. A future WhatsApp adapter may receive complete Ogg/Opus media rather than realtime PCM and will need to download, decode, and stream that media into the transcription boundary.
- Exposing the ElevenLabs session identifier in `ready` is useful for prototype debugging but introduces a small amount of provider coupling in an otherwise provider-neutral client contract. This can be replaced with an application-owned session identifier later if needed.
- VAD only commits while the upstream session remains active. The CLI and future clients must wait for `final_transcript` before disconnecting when they require the completed text.
- Omitting an application-level chunk-size limit is a prototype decision. Production hardening should establish explicit message and decoded-audio limits before exposing the endpoint to untrusted clients.
