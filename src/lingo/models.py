"""SQLAlchemy models for calls and learning memory."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Learner(Base):
    __tablename__ = "learners"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CallSession(Base):
    __tablename__ = "call_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    external_call_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), index=True)
    prompt_version: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    praise: Mapped[str | None] = mapped_column(Text)
    reusable_item: Mapped[str | None] = mapped_column(Text)
    language_evidence: Mapped[str | None] = mapped_column(Text)
    proficiency_evidence: Mapped[str | None] = mapped_column(Text)
    turns: Mapped[list[Turn]] = relationship(back_populates="session", cascade="all, delete-orphan")


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_turn_session_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    session: Mapped[CallSession] = relationship(back_populates="turns")


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    source_turn_id: Mapped[int] = mapped_column(ForeignKey("turns.id"), index=True)
    original: Mapped[str] = mapped_column(Text)
    corrected: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    pattern_key: Mapped[str | None] = mapped_column(String(160), index=True)
    language_transfer: Mapped[str | None] = mapped_column(Text)


class LearningPattern(Base):
    __tablename__ = "learning_patterns"
    __table_args__ = (UniqueConstraint("learner_id", "pattern_key", name="uq_learner_pattern"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), index=True)
    pattern_key: Mapped[str] = mapped_column(String(160))
    example_original: Mapped[str] = mapped_column(Text)
    example_corrected: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    high_confidence_turns: Mapped[int] = mapped_column(Integer, default=0)
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
