from app.pipeline.dedup import cosine_similarity, slots_compatible


def test_cosine_identical_vectors_is_one() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_none_embedding_is_zero() -> None:
    assert cosine_similarity(None, [1.0, 0.0]) == 0.0


def test_slots_compatible_when_no_overlap() -> None:
    a = {"protocol": "SAML", "due_date": None}
    b = {"due_date": "2026-12-15", "protocol": None}
    assert slots_compatible(a, b) is True


def test_slots_incompatible_on_differing_stance() -> None:
    a = {"stance": "required"}
    b = {"stance": "deferred"}
    assert slots_compatible(a, b) is False


def test_slots_incompatible_on_differing_quantity() -> None:
    a = {"quantity": 500, "region": "eu-west-1"}
    b = {"quantity": 50, "region": "us-east-1"}
    assert slots_compatible(a, b) is False


def test_slots_compatible_identical_values() -> None:
    a = {"stance": "deferred", "extra": {"foo": "bar"}}
    b = {"stance": "deferred", "extra": {"foo": "bar"}}
    assert slots_compatible(a, b) is True


def test_corrects_bookkeeping_keys_never_conflict() -> None:
    a = {"stance": "deferred", "extra": {"corrects": True, "corrects_hint": "old text"}}
    b = {"stance": "deferred", "extra": {"corrects": False}}
    assert slots_compatible(a, b) is True
