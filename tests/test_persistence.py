import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from lingo.analysis import CallAnalysis, ExtractedCorrection
from lingo.database import SessionStore
from lingo.models import Base, CallSession, Correction, Learner, LearningPattern, Turn


def make_store():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    return SessionStore(factory), factory


def correction(sequence: int) -> ExtractedCorrection:
    return ExtractedCorrection(
        source_turn_sequence=sequence,
        original="since five years",
        corrected="for five years",
        reason="Use for with a duration.",
        category="grammar",
        confidence=0.9,
        pattern_key="present-perfect-duration",
    )


def test_turns_are_isolated_by_session_and_metadata_is_saved():
    store, factory = make_store()
    first = store.start_call("learner-a", "call-a", "prompt-v1", "gpt-6-luna")
    second = store.start_call("learner-a", "call-b", "prompt-v1", "gpt-6-luna")
    store.add_turn(first, "user", "first")
    store.add_turn(second, "user", "second")
    store.finish_call(first)

    with factory() as db:
        first_call = db.get(CallSession, first)
        assert first_call.model_name == "gpt-6-luna"
        assert first_call.ended_at is not None
        assert db.scalars(select(Turn).where(Turn.session_id == first)).one().content == "first"


def test_start_call_is_idempotent_for_external_call_id():
    store, factory = make_store()
    first = store.start_call("learner-a", "same-call", "v1", "gpt-6-luna")
    second = store.start_call("learner-a", "same-call", "v1", "gpt-6-luna")
    assert first == second
    with factory() as db:
        assert len(list(db.scalars(select(CallSession)))) == 1


def test_external_call_collision_does_not_create_orphan_learner():
    store, factory = make_store()
    store.start_call("learner-a", "same-call", "v1", "gpt-6-luna")
    with pytest.raises(ValueError, match="another learner"):
        store.start_call("learner-b", "same-call", "v1", "gpt-6-luna")
    with factory() as db:
        assert [learner.id for learner in db.scalars(select(Learner))] == ["learner-a"]


def test_two_high_confidence_instances_make_pattern_recurring():
    store, factory = make_store()
    first = store.start_call("learner-a", "call-a", "v1", "gpt-6-luna")
    store.add_turn(first, "user", "I work here since five years.")
    store.save_analysis(first, CallAnalysis("First", None, None, None, None, (correction(0),)))
    second = store.start_call("learner-a", "call-b", "v1", "gpt-6-luna")
    store.add_turn(second, "user", "I know her since two years.")
    store.save_analysis(second, CallAnalysis("Second", None, None, None, None, (correction(0),)))

    with factory() as db:
        pattern = db.scalars(select(LearningPattern)).one()
        assert pattern.high_confidence_turns == 2
        assert pattern.recurring is True


def test_same_turn_does_not_count_twice():
    store, factory = make_store()
    session_id = store.start_call("learner-a", "call-a", "v1", "gpt-6-luna")
    store.add_turn(session_id, "user", "I work here since five years.")
    store.save_analysis(
        session_id,
        CallAnalysis("Summary", None, None, None, None, (correction(0), correction(0))),
    )
    with factory() as db:
        pattern = db.scalars(select(LearningPattern)).one()
        assert pattern.high_confidence_turns == 1
        assert pattern.recurring is False


def test_reusable_item_is_available_without_recurring_pattern():
    store, _factory = make_store()
    session_id = store.start_call("learner-a", "call-a", "v1", "gpt-6-luna")
    store.save_analysis(
        session_id,
        CallAnalysis("Summary", None, "a useful phrase", None, None, ()),
    )
    memory, summary = store.prompt_memory("learner-a")
    assert memory == "a useful phrase"
    assert summary == "Summary"


def test_reanalysis_replaces_prior_corrections():
    store, factory = make_store()
    session_id = store.start_call("learner-a", "call-a", "v1", "gpt-6-luna")
    store.add_turn(session_id, "user", "I work here since five years.")
    store.save_analysis(
        session_id,
        CallAnalysis("First", None, None, None, None, (correction(0),)),
    )
    store.save_analysis(session_id, CallAnalysis("Revised", None, None, None, None, ()))
    with factory() as db:
        assert list(db.scalars(select(Correction))) == []
        assert list(db.scalars(select(LearningPattern))) == []
        assert db.get(CallSession, session_id).summary == "Revised"


def test_reanalysis_refreshes_pattern_example():
    store, factory = make_store()
    session_id = store.start_call("learner-a", "call-a", "v1", "gpt-6-luna")
    store.add_turn(session_id, "user", "I know her since two years.")
    store.save_analysis(
        session_id,
        CallAnalysis("First", None, None, None, None, (correction(0),)),
    )
    revised = ExtractedCorrection(
        source_turn_sequence=0,
        original="since two years",
        corrected="for two years",
        reason="Use for before a length of time.",
        category="grammar",
        confidence=0.95,
        pattern_key="present-perfect-duration",
    )
    store.save_analysis(
        session_id,
        CallAnalysis("Revised", None, None, None, None, (revised,)),
    )
    with factory() as db:
        pattern = db.scalars(select(LearningPattern)).one()
        assert pattern.example_original == "since two years"
        assert pattern.example_corrected == "for two years"
        assert pattern.reason == "Use for before a length of time."
