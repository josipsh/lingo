"""Database setup and session persistence."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from lingo.models import CallSession, Learner, LearningPattern, Turn, utcnow


def create_session_factory(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    if not database_url:
        raise ValueError("DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    return engine, sessionmaker(engine, expire_on_commit=False)


class SessionStore:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def start_call(
        self, learner_id: str, external_call_id: str, prompt_version: str, model_name: str
    ) -> str:
        with self._session_factory() as db:
            values = {"id": learner_id, "created_at": utcnow()}
            if db.bind and db.bind.dialect.name == "postgresql":
                statement = postgresql_insert(Learner).values(**values).on_conflict_do_nothing()
            elif db.bind and db.bind.dialect.name == "sqlite":
                statement = sqlite_insert(Learner).values(**values).on_conflict_do_nothing()
            else:
                raise ValueError("Lingo requires PostgreSQL or SQLite")
            db.execute(statement)
            call_id = str(uuid.uuid4())
            call_values = {
                "id": call_id,
                "learner_id": learner_id,
                "external_call_id": external_call_id,
                "prompt_version": prompt_version,
                "model_name": model_name,
                "status": "active",
                "started_at": utcnow(),
            }
            if db.bind.dialect.name == "postgresql":
                call_statement = (
                    postgresql_insert(CallSession)
                    .values(**call_values)
                    .on_conflict_do_nothing(index_elements=[CallSession.external_call_id])
                )
            else:
                call_statement = (
                    sqlite_insert(CallSession)
                    .values(**call_values)
                    .on_conflict_do_nothing(index_elements=[CallSession.external_call_id])
                )
            db.execute(call_statement)
            stored_call = db.execute(
                select(CallSession.id, CallSession.learner_id).where(
                    CallSession.external_call_id == external_call_id
                )
            ).one_or_none()
            if stored_call is None:
                raise RuntimeError("call session could not be created")
            if stored_call.learner_id != learner_id:
                raise ValueError("external call ID belongs to another learner")
            db.commit()
            return stored_call.id

    def add_turn(self, session_id: str, role: str, content: str) -> int:
        content = content.strip()
        if role not in {"user", "assistant"} or not content:
            raise ValueError("a completed user or assistant turn is required")
        with self._session_factory() as db:
            call = db.scalar(
                select(CallSession)
                .where(CallSession.id == session_id)
                .with_for_update()
            )
            if call is None:
                raise ValueError("call session does not exist")
            latest = db.scalar(
                select(func.max(Turn.sequence)).where(Turn.session_id == session_id)
            )
            sequence = 0 if latest is None else latest + 1
            turn = Turn(session_id=session_id, sequence=sequence, role=role, content=content)
            db.add(turn)
            db.commit()
            return turn.id

    def finish_call(self, session_id: str, *, failed: bool = False) -> None:
        with self._session_factory() as db:
            call = db.get(CallSession, session_id)
            if call is None:
                return
            call.status = "failed" if failed else "completed"
            call.ended_at = utcnow()
            db.commit()

    def transcript(self, session_id: str) -> list[Turn]:
        with self._session_factory() as db:
            return list(
                db.scalars(
                    select(Turn)
                    .where(Turn.session_id == session_id)
                    .order_by(Turn.sequence)
                )
            )

    def save_analysis(self, session_id: str, analysis) -> None:
        from lingo.analysis import apply_analysis

        with self._session_factory() as db:
            apply_analysis(db, session_id, analysis)

    def prompt_memory(self, learner_id: str) -> tuple[str | None, str | None]:
        with self._session_factory() as db:
            pattern = db.scalars(
                select(LearningPattern)
                .where(
                    LearningPattern.learner_id == learner_id,
                    LearningPattern.recurring.is_(True),
                )
                .order_by(LearningPattern.updated_at.desc())
                .limit(1)
            ).first()
            prior = db.scalars(
                select(CallSession)
                .where(CallSession.learner_id == learner_id, CallSession.summary.is_not(None))
                .order_by(CallSession.ended_at.desc())
                .limit(1)
            ).first()
            memory = None
            if pattern:
                memory = (
                    f"{pattern.example_original} -> {pattern.example_corrected}. {pattern.reason}"
                )
            elif prior and prior.reusable_item:
                memory = prior.reusable_item
            return memory, prior.summary if prior else None
