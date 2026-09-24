from lingo.identity import learner_id


def test_learner_id_is_deterministic_protected_and_normalized():
    first = learner_id("+1 (202) 555-0100", "secret")
    assert first == learner_id("12025550100", "secret")
    assert first != learner_id("12025550100", "different-secret")
    assert "12025550100" not in first
    assert len(first) == 64
