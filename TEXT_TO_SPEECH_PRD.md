# Realtime transcription and text-to-speech prototype

## Problem Statement

People who practice a language while walking, commuting, exercising, or doing everyday activities need a voice-first experience that does not require continuous screen interaction. The backend already accepts streamed audio and returns provisional and committed transcripts, but the interaction stops at text. The client cannot hear a spoken result.

The first speech-generation version needs to turn every non-empty committed transcript into an audio clip. For now, the generated speech will repeat the committed transcript exactly. Later versions will generate different text, such as a correction or conversational response, before synthesis. The initial design must make that text-producing step easy to replace without coupling speech generation to transcription internals.

The first client remains the command-line client. It must exercise the complete flow without exposing ElevenLabs credentials, calling a paid provider from automated tests, or retaining data on the server. A future WhatsApp adapter is expected, but its media-delivery behavior is not part of this version.

## Solution

Extend the FastAPI WebSocket service so each non-empty committed transcript starts an ElevenLabs text-to-speech job. Send the committed transcript to the client immediately, then stream the resulting MP3 bytes through typed JSON events on the same WebSocket. Correlate the transcript, audio chunks, completion, and failure events with an application-generated clip identifier.

Keep realtime speech-to-text and text-to-speech behind separate provider boundaries. An orchestration step will choose the text passed to speech generation. It will use the committed transcript unchanged in this version. A later correction, response-generation, or context-building step can replace that choice without changing the speech provider or public audio event contract.

Run one speech-generation job at a time for each client connection. Queue later commits in arrival order while generation is active, but continue receiving source audio and forwarding transcription events. A speech-generation failure will end only its clip and will not close the transcription session or prevent later queued clips from running.

The command-line client will collect all streamed chunks for each clip and save one MP3 file per committed transcript in the current directory. It will wait for the final queued clip to finish or fail before disconnecting. It will preserve bytes from an interrupted stream in one partial MP3 file, print the error, and exit successfully.

## User Stories

1. As a language learner, I want my speech transcribed while I speak, so that I can practice without interacting with a screen.
2. As a language learner, I want provisional text returned quickly, so that the interaction remains responsive.
3. As a language learner, I want a stable transcript after I pause, so that completed speech can trigger the next step.
4. As a language learner, I want each committed utterance turned into speech, so that I can hear the result without reading the screen.
5. As a language learner, I want this version to repeat my committed transcript exactly, so that the first speech flow is predictable.
6. As a language learner, I want transcript delivery to remain fast while speech is generated, so that text-to-speech latency does not delay transcription feedback.
7. As a language learner, I want utterances spoken in commit order, so that generated clips follow the order in which I spoke.
8. As a client developer, I want to stream input audio incrementally over one WebSocket, so that long sessions do not require uploading complete recordings.
9. As a client developer, I want provider-neutral input and output events, so that the client is not coupled to ElevenLabs payloads.
10. As a client developer, I want distinct partial and final transcript events, so that provisional text is not mistaken for committed text.
11. As a client developer, I want a clip identifier on each synthesized transcript and speech event, so that one utterance can be tracked through generation.
12. As a client developer, I want a speech-start event before audio chunks, so that I can initialize clip-specific state.
13. As a client developer, I want speech bytes streamed in chunks, so that a future client can reduce time to first audio.
14. As a client developer, I want a speech-end event, so that I know the MP3 file is complete.
15. As a client developer, I want a clip-specific speech-error event, so that one failed synthesis can be handled without losing transcription.
16. As a client developer, I want speech events to identify their audio format, so that I do not need to infer how to decode the bytes.
17. As a client developer, I want whitespace-only committed transcripts to skip synthesis, so that empty utterances do not create files or spend provider credits.
18. As a client developer, I want malformed input to produce a structured error without ending the session, so that one bad message does not interrupt valid audio.
19. As a client developer, I want fatal transcription failures to produce a structured error before disconnection, so that provider failure is distinguishable from network loss.
20. As a client developer, I want speech-generation failures to leave the WebSocket connected, so that later utterances can still be transcribed and synthesized.
21. As a client developer, I want safe high-level errors, so that provider payloads and credentials are not exposed.
22. As a client developer, I want a ready event after the upstream transcription session starts, so that I know when input audio can be processed.
23. As a developer, I want speech input selection separate from speech synthesis, so that a later generated response can replace the verbatim transcript.
24. As a developer, I want transcription and speech providers to have separate dependency boundaries, so that either integration can be tested or replaced independently.
25. As a developer, I want the ElevenLabs API key and voice identifier loaded from server configuration, so that clients never receive provider credentials or choose arbitrary voices.
26. As a developer, I want a low-latency multilingual speech model by default, so that the prototype responds quickly and can follow configured transcription languages.
27. As a developer, I want provider-default voice tuning, so that the first version does not expose untested stability, similarity, style, or speed controls.
28. As a developer, I want automated tests to use fake providers, so that tests are deterministic and do not consume ElevenLabs credits.
29. As a developer, I want tests to connect through the real FastAPI WebSocket endpoint, so that they validate the public event flow.
30. As a developer, I want the CLI to save one MP3 per commit, so that each generated result can be inspected independently.
31. As a developer, I want the CLI to preserve a partially received MP3 when synthesis fails, so that received provider output is not discarded.
32. As a developer, I want the CLI to wait for speech generation after source audio ends, so that it does not disconnect before the final clip arrives.
33. As a developer, I want a failed final clip reported without a nonzero process exit, so that this prototype treats successful transcription as a successful run.
34. As an operator, I want active and queued speech work canceled when the client disconnects, so that abandoned sessions stop consuming resources.
35. As an operator, I want detailed provider errors logged on the server, so that authentication, quota, concurrency, and transport failures can be diagnosed.
36. As an operator, I want each connection to own its transcription session and speech queue, so that concurrent users do not share in-memory state.
37. As an operator, I want the lack of client authentication and generation limits documented, so that paid endpoint exposure is understood before deployment.
38. As a WhatsApp integration developer, I want speech generation isolated from client delivery, so that a future adapter can translate completed clips into WhatsApp media without redesigning synthesis.

## Implementation Decisions

- The service will continue to expose one FastAPI WebSocket endpoint at `/ws/transcription`.
- The endpoint will not require client authentication in this prototype.
- Each accepted client WebSocket will own one realtime transcription session, one in-memory speech queue, and at most one active speech-generation request.
- Production speech-to-text will continue to use the ElevenLabs Realtime Speech-to-Text WebSocket protocol.
- Production text-to-speech will use the ElevenLabs HTTP streaming endpoint because the complete text is available when a transcript is committed. The server will relay returned audio bytes as they arrive.
- The application will not use the ElevenLabs SDK. Provider integrations will use direct protocol clients and explicit application types.
- Transcription and speech synthesis will use separate provider interfaces. The application constructor will accept both dependencies.
- The speech provider will accept text and speech configuration and yield audio byte chunks. It will not know whether its input came from a transcript, correction, language model, or another source.
- The orchestration layer will pass committed transcript text directly to the speech provider in this version. This is the single step a later feature will replace when synthesized text differs from the transcript.
- The transcription model will default to `scribe_v2_realtime` and will use voice activity detection to commit utterances.
- The speech model will default to `eleven_flash_v2_5`.
- The speech voice ID will come from the required `ELEVENLABS_VOICE_ID` environment variable.
- The existing server-side ElevenLabs API key will authenticate both provider integrations.
- Speech synthesis will use the configured transcription language as the ElevenLabs language code.
- ElevenLabs provider defaults will control voice stability, similarity, style, speaker boost, and speed.
- The server will explicitly request `mp3_44100_128`. The client contract will call this format `mp3_44100_128` rather than relying on an undocumented or inferred default.
- Input audio will remain signed 16-bit little-endian mono PCM at 16 kHz, represented as `pcm_16000`.
- Configuration will remain a standard-library dataclass populated from environment variables. Application code will not use Pydantic for settings or message validation.
- Provider endpoint URLs may be configurable for tests and non-production environments. Production defaults will point to the documented ElevenLabs endpoints.
- The server will continue to accept only JSON text frames with this client payload:

  ```json
  {
    "type": "audio_chunk",
    "audio": "<base64-encoded PCM bytes>"
  }
  ```

- The input `type` must equal `audio_chunk`, and `audio` must be a valid, non-empty base64 string. Invalid client messages are recoverable and do not close the connection.
- The server will not add an application-level decoded input chunk-size limit in this prototype.
- The existing `ready` and `partial_transcript` event shapes will remain unchanged.
- A non-empty committed transcript will receive an application-generated clip ID and will be sent immediately:

  ```json
  {
    "type": "final_transcript",
    "clip_id": "<application-generated identifier>",
    "text": "<stable transcription>"
  }
  ```

- A whitespace-only committed transcript will still be forwarded as `final_transcript`, but it will not have a clip ID and will not enqueue speech generation.
- The server will enqueue speech generation only after sending the final transcript. Enqueueing must not block consumption of later transcription events or client audio.
- Speech jobs will run serially in commit order for each connection. This policy is intentionally isolated so a later version can cancel an older job and use its context when a newer commit extends the user's input.
- Before requesting or relaying speech for a queued job, the server will send:

  ```json
  {
    "type": "speech_start",
    "clip_id": "<matching identifier>",
    "format": "mp3_44100_128"
  }
  ```

- Each non-empty provider audio chunk will be sent as:

  ```json
  {
    "type": "speech_chunk",
    "clip_id": "<matching identifier>",
    "audio": "<base64-encoded MP3 bytes>"
  }
  ```

- Successful stream completion will be sent as:

  ```json
  {
    "type": "speech_end",
    "clip_id": "<matching identifier>"
  }
  ```

- A failed speech stream will terminate that clip with:

  ```json
  {
    "type": "speech_error",
    "clip_id": "<matching identifier>",
    "code": 502,
    "message": "Speech generation unavailable"
  }
  ```

- Speech errors will not close the client WebSocket. After sending `speech_error`, the worker will continue with the next queued clip.
- The server will not retry failed speech requests in this version.
- The existing general `error` event will remain reserved for invalid client input and fatal transcription or pipeline failures. HTTP-like codes inside WebSocket JSON are application codes, not HTTP response statuses.
- Raw exceptions, provider response bodies, request headers, credentials, and internal endpoint details will not be sent to clients. The server will log details needed for diagnosis, including provider request identifiers when available.
- A normal client disconnect will close the transcription session, cancel active speech generation, and discard queued speech jobs. No server-side audio or transcript artifact will survive the connection.
- The CLI will continue to use FFmpeg to convert its source MP3 to realtime `pcm_16000` input. FFmpeg is not needed to assemble the returned MP3 chunks.
- The CLI will collect chunks by clip ID and write one completed `.mp3` file per commit in the current working directory. File names will include the clip ID so concurrent or repeated runs do not depend on a shared counter.
- The CLI will not play generated clips automatically.
- If `speech_error` arrives after one or more chunks, the CLI will combine all received bytes for that clip into one `.partial.mp3` file. If no bytes arrived, it will not create an empty file.
- After source audio finishes, the CLI will remain connected until VAD produces the expected final transcript and that transcript's last queued clip reaches `speech_end` or `speech_error`.
- A final `speech_error` will be printed, but the CLI process will exit with status zero. Existing command, transport, transcription, and local file errors may still produce a nonzero exit.
- The server will not persist input audio, transcripts, generated speech, queue state, or session metadata.
- The client remains responsible for any local MP3 files it creates.

## Testing Decisions

- Tests will focus on externally observable behavior through the real FastAPI WebSocket endpoint. They will not assert private queue or transport implementation details.
- The highest test seam will remain application construction. Tests will supply an in-memory transcription provider and an in-memory speech provider together, rather than monkeypatching network clients.
- No automated test will use a real ElevenLabs API key, make an external network request, or consume paid credits.
- The critical path will emit a committed transcript from the fake transcription provider, assert that `final_transcript` arrives before speech events, stream several chunks from the fake speech provider, and assert `speech_start`, ordered `speech_chunk` events, and `speech_end` with one matching clip ID.
- A pass-through test will verify that the exact committed transcript text reaches the speech provider.
- A responsiveness test will hold one speech request open while emitting more transcription events and sending more input audio. It will verify that transcription and input handling continue while synthesis runs.
- A queue test will emit multiple commits while the first speech stream is active and verify that the fake speech provider starts jobs one at a time in commit order.
- A whitespace test will verify that a whitespace-only final transcript is forwarded but does not call the speech provider or emit speech events.
- A speech-failure test will make the fake provider fail after yielding bytes, assert a safe `speech_error`, and verify that the WebSocket remains usable and the next queued clip runs.
- A disconnect test will verify that disconnecting closes the transcription provider, cancels active speech generation, and prevents queued jobs from starting.
- Existing malformed-input coverage will remain. Malformed JSON, unknown message types, missing audio, incorrect field types, invalid base64, and empty audio must return recoverable errors.
- Existing fatal-transcription coverage will remain. Fatal provider errors must produce a safe error and close the WebSocket with an appropriate server-error close code.
- Provider contract tests will verify the ElevenLabs TTS URL, API-key header, voice ID, Flash v2.5 model, configured language, explicit MP3 output format, request text, chunk streaming, and safe translation of provider failures without making a live request.
- Configuration tests will verify that the API key and voice ID are required, defaults are correct, and explicit values override defaults.
- CLI tests will verify that chunks with one clip ID become one MP3, separate commits become separate files, completed files use the `.mp3` suffix, interrupted files use `.partial.mp3`, empty failed streams create no file, and the final speech error exits with status zero.
- CLI lifecycle tests will verify that it does not disconnect after `final_transcript` and waits for the corresponding terminal speech event.
- Live ElevenLabs integration tests are not part of the automated suite.

## Out of Scope

- Correcting, translating, summarizing, or otherwise changing committed transcript text before synthesis.
- Generating an AI response or maintaining conversation context.
- Canceling an active older speech job when a newer commit arrives.
- Combining context from canceled and newer commits.
- Parallel speech generation within one client connection.
- Retrying text-to-speech requests.
- Per-client voice selection or voice tuning.
- Speech speed, stability, similarity, style, speaker boost, pronunciation dictionaries, and deterministic seeds.
- Speech timestamps, alignment data, subtitles, or word highlighting.
- Automatic playback by the command-line client.
- Combining multiple committed utterances into one session-level MP3.
- Converting returned MP3 clips to WAV, PCM, Opus, or another format.
- Server-side storage, downloadable media URLs, object storage, or replay of generated clips.
- WhatsApp API integration, webhook handling, media upload, and WhatsApp-specific audio adaptation.
- A browser, mobile, or graphical client.
- Client authentication, authorization, user accounts, rate limiting, per-session generation limits, credit budgets, and billing controls.
- Production deployment, horizontal scaling, shared queues, durable jobs, metrics, tracing, and alerting.
- Supporting multiple input audio formats in the server endpoint.
- Per-session transcription language or input-format selection.
- Manual transcript commit on disconnect.
- A live-provider test in the default automated test suite.

## Further Notes

- ElevenLabs recommends HTTP streaming when the full text is available before synthesis. The WebSocket text-to-speech endpoint is intended for text that arrives incrementally and is not needed for committed transcripts.
- `eleven_flash_v2_5` is selected for its low documented latency, multilingual support, lower per-character price, and higher concurrency allowance than quality-focused models. Provider pricing, model availability, and limits can change and must be checked before production release.
- The explicit output format is `mp3_44100_128`, which is also ElevenLabs' current default. Requesting it explicitly prevents a provider default change from silently changing the public client contract.
- Streaming reduces time to first audio but does not guarantee that a failed partial MP3 is playable. The CLI preserves partial bytes for inspection without promising successful decoding.
- The unauthenticated endpoint can spend speech-to-text and text-to-speech credits. This is accepted for the local prototype only. Authentication, quotas, and generation limits are required before exposure to untrusted clients.
- Queueing complete committed utterances is the current policy, not a permanent conversation model. The queue coordinator should remain separate from provider transport so a later feature can cancel an older response and carry its context into a newer response.
- The API key must remain server-side. A restricted ElevenLabs key with endpoint permissions and a credit quota is preferred even during prototype development.
- Official references used for this design include the ElevenLabs text-to-speech streaming endpoint, streaming concepts, model documentation, authentication guide, and API error guide.
