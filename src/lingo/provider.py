from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class TranscriptionConfig:
    model: str
    language: str
    audio_format: str
    commit_strategy: Literal["vad"] = "vad"


@dataclass(frozen=True)
class ProviderEvent:
    type: Literal["ready", "partial", "final"]
    text: str | None = None
    session_id: str | None = None


class ProviderError(Exception):
    """A fatal error from the transcription provider."""


class TranscriptionSession(Protocol):
    async def send_audio(self, audio: bytes) -> None: ...

    def events(self) -> AsyncIterator[ProviderEvent]: ...

    async def close(self) -> None: ...


class TranscriptionProvider(Protocol):
    async def connect(self, config: TranscriptionConfig) -> TranscriptionSession: ...
