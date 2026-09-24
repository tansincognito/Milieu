from app.pipeline.lifecycle import stances_conflict


def test_required_vs_deferred_conflicts() -> None:
    assert stances_conflict("required", "deferred") is True


def test_required_vs_in_progress_is_ok() -> None:
    assert stances_conflict("required", "in_progress") is False


def test_deferred_vs_in_progress_conflicts() -> None:
    assert stances_conflict("deferred", "in_progress") is True


def test_matrix_is_symmetric() -> None:
    assert stances_conflict("in_progress", "deferred") is True
    assert stances_conflict("deferred", "required") is True


def test_required_vs_dropped_conflicts() -> None:
    assert stances_conflict("required", "dropped") is True


def test_deferred_vs_dropped_is_ok() -> None:
    assert stances_conflict("deferred", "dropped") is False


def test_missing_stance_never_conflicts() -> None:
    assert stances_conflict(None, "deferred") is False
    assert stances_conflict("required", None) is False
