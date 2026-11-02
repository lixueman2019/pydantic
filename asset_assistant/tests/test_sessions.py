import asyncio

import pytest

from asset_assistant.config import Settings
from asset_assistant.contracts import AppError
from asset_assistant.sessions import SessionStore


def test_ttl_capacity_and_history():
    now = [0.0]
    store = SessionStore(Settings(max_sessions=1, session_ttl_seconds=2, max_history_turns=1), lambda: now[0])
    session = store.create()
    with pytest.raises(AppError) as error:
        store.create()
    assert error.value.status == 503
    store.add_turn(session, "a", "b")
    store.add_turn(session, "c", "d")
    assert session.history == [("c", "d")]
    now[0] = 3
    store.reap()
    assert session.deleted
    assert not session.history
    assert store.create().session_id != session.session_id


def test_mutual_exclusion_and_chart_isolation():
    store = SessionStore(Settings())
    first, second = store.create(), store.create()
    store.acquire(first.session_id)
    with pytest.raises(AppError) as error:
        store.acquire(first.session_id)
    assert error.value.status == 409
    store.release(first)
    chart_id = store.add_chart(first, b"\x89PNG\r\n\x1a\nexample")
    assert chart_id not in second.charts
    store.delete(first.session_id)
    assert not first.charts


async def test_delete_cancels_running_task():
    store = SessionStore(Settings())
    session = store.acquire(store.create().session_id)
    session.task = asyncio.create_task(asyncio.Event().wait())
    store.delete(session.session_id)
    with pytest.raises(asyncio.CancelledError):
        await session.task