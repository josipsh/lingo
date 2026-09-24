"""Post-call structured analysis and learning-memory updates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.orm import Session

from lingo.models import CallSession, Correction, Learner, LearningPattern, Turn, utcnow

ANALYSIS_PROMPT_VERSION = "post-call-v1"
HIGH_CONFIDENCE = 0.8
PATTERN_KEYS = {
    "agreement",
    "article-use",
    "collocation",
    "countability",
    "preposition-duration",
    "present-perfect-duration",
    "tense",
    "verb-complement",
    "word-choice",
    "word-order",
}


@dataclass(frozen=True)
class ExtractedCorrection:
    source_turn_sequence: int
    original: str
    corrected: str
    reason: str
    category: str
    confidence: float
    pattern_key: str | None = None
    language_transfer: str | None = None


@dataclass(frozen=True)
class CallAnalysis:
    summary: str
    praise: str | None
    reusable_item: str | None
    language_evidence: str | None
    proficiency_evidence: str | None
    corrections: tuple[ExtractedCorrection, ...]


def parse_analysis(payload: str | dict[str, Any]) -> CallAnalysis:
    """Validate model JSON before it reaches learning memory."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        raise ValueError("analysis must include a summary")
    raw_corrections = data.get("corrections", [])
    if not isinstance(raw_corrections, list):
        raise ValueError("corrections must be a list")
    corrections = []
    for item in raw_corrections:
        if not isinstance(item, dict):
            raise ValueError("each correction must be an object")
        category = item.get("category")
        confidence = item.get("confidence")
        if category not in {"meaning", "grammar", "naturalness"}:
            raise ValueError(f"invalid correction category: {category}")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("correction confidence must be between 0 and 1")
        sequence = item.get("source_turn_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("source turn sequence must be a non-negative integer")
        pattern_key = item.get("pattern_key")
        if pattern_key is not None and pattern_key not in PATTERN_KEYS:
            raise ValueError(f"invalid pattern key: {pattern_key}")
        corrections.append(
            ExtractedCorrection(
                source_turn_sequence=sequence,
                original=_required_text(item, "original"),
                corrected=_required_text(item, "corrected"),
                reason=_required_text(item, "reason"),
                category=category,
                confidence=float(confidence),
                pattern_key=pattern_key,
                language_transfer=(str(item["language_transfer"]).strip() or None)
                if item.get("language_transfer") is not None
                else None,
            )
        )
    return CallAnalysis(
        summary=data["summary"].strip(),
        praise=_optional_text(data.get("praise")),
        reusable_item=_optional_text(data.get("reusable_item")),
        language_evidence=_optional_text(data.get("language_evidence")),
        proficiency_evidence=_optional_text(data.get("proficiency_evidence")),
        corrections=tuple(corrections),
    )


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _required_text(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"correction {field} must be non-empty text")
    return value.strip()


def apply_analysis(db: Session, session_id: str, analysis: CallAnalysis) -> None:
    """Save extraction and mark patterns recurring after two distinct user turns."""
    call = db.get(CallSession, session_id)
    if call is None:
        raise ValueError("call session does not exist")
    db.execute(select(Learner).where(Learner.id == call.learner_id).with_for_update())
    call.summary = analysis.summary
    call.praise = analysis.praise
    call.reusable_item = analysis.reusable_item
    call.language_evidence = analysis.language_evidence
    call.proficiency_evidence = analysis.proficiency_evidence

    touched_patterns = set(
        db.scalars(
            select(Correction.pattern_key).where(
                Correction.session_id == session_id,
                Correction.pattern_key.is_not(None),
            )
        )
    )
    db.execute(delete(Correction).where(Correction.session_id == session_id))

    turns = {
        turn.sequence: turn
        for turn in db.scalars(
            select(Turn).where(Turn.session_id == session_id, Turn.role == "user")
        )
    }
    for item in analysis.corrections:
        turn = turns.get(item.source_turn_sequence)
        if turn is None:
            continue
        db.add(
            Correction(
                session_id=session_id,
                source_turn_id=turn.id,
                original=item.original,
                corrected=item.corrected,
                reason=item.reason,
                category=item.category,
                confidence=item.confidence,
                pattern_key=item.pattern_key,
                language_transfer=item.language_transfer,
            )
        )
        if item.pattern_key and item.confidence >= HIGH_CONFIDENCE:
            touched_patterns.add(item.pattern_key)
            pattern = db.scalar(
                select(LearningPattern).where(
                    LearningPattern.learner_id == call.learner_id,
                    LearningPattern.pattern_key == item.pattern_key,
                )
            )
            if pattern is None:
                pattern = LearningPattern(
                    learner_id=call.learner_id,
                    pattern_key=item.pattern_key,
                    example_original=item.original,
                    example_corrected=item.corrected,
                    reason=item.reason,
                )
                db.add(pattern)
            else:
                pattern.example_original = item.original
                pattern.example_corrected = item.corrected
                pattern.reason = item.reason
    db.flush()

    for pattern_key in touched_patterns:
        count = db.scalar(
            select(func.count(distinct(Correction.source_turn_id)))
            .join(CallSession, Correction.session_id == CallSession.id)
            .where(
                CallSession.learner_id == call.learner_id,
                Correction.pattern_key == pattern_key,
                Correction.confidence >= HIGH_CONFIDENCE,
            )
        ) or 0
        pattern = db.scalar(
            select(LearningPattern).where(
                LearningPattern.learner_id == call.learner_id,
                LearningPattern.pattern_key == pattern_key,
            )
        )
        if pattern:
            if count:
                pattern.high_confidence_turns = count
                pattern.recurring = count >= 2
                pattern.updated_at = utcnow()
            else:
                db.delete(pattern)
    db.commit()


class AnalysisClient(Protocol):
    async def analyze(self, transcript: list[Turn]) -> CallAnalysis: ...


class OpenAIAnalysisClient:
    def __init__(self, api_key: str, model: str):
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def analyze(self, transcript: list[Turn]) -> CallAnalysis:
        numbered = "\n".join(
            f"{turn.sequence} {turn.role}: {turn.content}" for turn in transcript
        )
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Analyze an English practice call. Return JSON only. Do not infer "
                        "pronunciation. Include only worthwhile corrections grounded in user "
                        "turns. Language-background evidence must be cautious and repeated. "
                        f"Analysis policy version: {ANALYSIS_PROMPT_VERSION}."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Return summary, praise, reusable_item, language_evidence, "
                        "proficiency_evidence, and corrections. Each correction needs "
                        "source_turn_sequence, original, corrected, reason, category "
                        "(meaning, grammar, or naturalness), confidence from 0 to 1, "
                        "pattern_key, and language_transfer. pattern_key must be null or one of: "
                        f"{', '.join(sorted(PATTERN_KEYS))}. Use the same canonical key for "
                        "equivalent patterns. Use null when optional evidence is absent. "
                        f"Transcript:\n{numbered}"
                    ),
                },
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("OpenAI returned an empty analysis")
        return parse_analysis(content)
