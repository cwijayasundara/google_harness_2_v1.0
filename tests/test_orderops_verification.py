import pytest

from harness_clone.storage import RunStore


def editor_records(tmp_path):
    store = RunStore(tmp_path / "experiment")
    store.put("orderops", "tasks", {"dev": [{"id": "dev-000"}]})
    for arm in ("local", "persistent"):
        for scope in ("INV", "DOM", "COMPOSE"):
            store.put("edit", f"{arm}-dev-000/stage/{scope}", {
                "scope": scope, "status": "keep_base", "error": "",
            })
    store.put("orderops-reflection", "persistent/dev-000", {"entry": {"lesson": "check inputs"}})
    return store


def test_live_verification_accepts_valid_keep_base_without_claiming_improvement(tmp_path):
    from harness_clone.orderops.verification import verify_live_rsi

    store = editor_records(tmp_path)
    assert verify_live_rsi(store.root) == {
        "editing_stages": 6, "accepted_changes": 0, "keep_base": 6, "reflections": 1,
    }


def test_live_verification_rejects_silent_pi_fallback(tmp_path):
    from harness_clone.orderops.verification import verify_live_rsi

    store = editor_records(tmp_path)
    store.put("edit", "local-dev-000/stage/INV", {
        "scope": "INV", "status": "fallback", "error": "no public port published",
    })
    with pytest.raises(RuntimeError, match="fallback.*no public port"):
        verify_live_rsi(store.root)


def test_live_verification_requires_each_editor_scope(tmp_path):
    from harness_clone.orderops.verification import verify_live_rsi

    store = editor_records(tmp_path)
    with store.transaction() as db:
        db.execute("delete from records where kind='edit' and id like 'persistent%COMPOSE'")
    with pytest.raises(RuntimeError, match="persistent.*COMPOSE"):
        verify_live_rsi(store.root)


def test_live_verification_rejects_failed_reflection(tmp_path):
    from harness_clone.orderops.verification import verify_live_rsi

    store = editor_records(tmp_path)
    store.put("orderops-reflection", "persistent/dev-000", {"error": "RuntimeError"})
    with pytest.raises(RuntimeError, match="reflection"):
        verify_live_rsi(store.root)
