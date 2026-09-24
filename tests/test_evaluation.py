from lingo.evaluation import load_cases


def test_evaluation_set_has_five_cases_per_background():
    cases = load_cases()
    counts = {}
    for case in cases:
        counts[case["background"]] = counts.get(case["background"], 0) + 1
        assert "must_correct" in case
        assert "leave_alone" in case
        assert case["context"]
    assert sorted(counts.values()) == [5, 5, 5]
