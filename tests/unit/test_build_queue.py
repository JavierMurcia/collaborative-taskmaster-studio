from __future__ import annotations

from infrastructure.local.build_queue import JsonBuildQueueStore


def test_json_build_queue_is_owner_scoped_and_lists_pending(tmp_path) -> None:
    queue = JsonBuildQueueStore(tmp_path)
    payload = {
        "build_id": "build_1234567890abcdef",
        "owner_session_id": "owner_one",
        "state": "queued",
        "updated_at": "2026-08-28T00:00:00+00:00",
    }
    queue.save("build_1234567890abcdef", payload)

    assert queue.load("build_1234567890abcdef", "owner_two") is None
    assert queue.load("build_1234567890abcdef", "owner_one") == payload
    assert queue.list_pending() == (payload,)

    queue.save("build_1234567890abcdef", {**payload, "state": "completed"})
    assert queue.list_pending() == ()

