"""Bounded, process-local state. All mutations run on the application's event loop."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

from .config import Settings
from .contracts import AppError, Citation, ErrorCode


def opaque_id() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class Session:
    session_id: str
    touched_at: float
    history: list[tuple[str, str]] = field(default_factory=list)
    citations: dict[str, Citation] = field(default_factory=dict)
    charts: dict[str, bytes] = field(default_factory=dict)
    task: asyncio.Task | None = None
    busy: bool = False
    deleted: bool = False


class SessionStore:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.clock = clock
        self._sessions: dict[str, Session] = {}

    @staticmethod
    def _dispose(session: Session) -> None:
        session.deleted = True
        if session.task is not None and not session.task.done():
            session.task.cancel()
        session.history.clear()
        session.citations.clear()
        session.charts.clear()

    def reap(self) -> None:
        now = self.clock()
        for key, session in list(self._sessions.items()):
            # A run has its own hard timeout; idle TTL starts after release.
            if not session.busy and now - session.touched_at >= self.settings.session_ttl_seconds:
                self._dispose(self._sessions.pop(key))

    def create(self) -> Session:
        self.reap()
        if len(self._sessions) >= self.settings.max_sessions:
            raise AppError(ErrorCode.CAPACITY_EXCEEDED, "会话已满，请清空旧会话。", 503)
        session = Session(opaque_id(), self.clock())
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        self.reap()
        session = self._sessions.get(session_id)
        if session is None or session.deleted:
            raise AppError(ErrorCode.NOT_FOUND, "会话不存在或已过期。", 404)
        session.touched_at = self.clock()
        return session

    def acquire(self, session_id: str) -> Session:
        session = self.get(session_id)
        if session.busy:
            raise AppError(ErrorCode.CONFLICT, "此会话已有正在运行的请求。", 409)
        # No await between checking and setting: atomic on one event loop.
        session.busy = True
        return session

    def release(self, session: Session) -> None:
        session.task = None
        session.busy = False
        session.touched_at = self.clock()

    def delete(self, session_id: str) -> None:
        session = self.get(session_id)
        self._sessions.pop(session_id)
        self._dispose(session)

    def add_turn(self, session: Session, question: str, answer: str) -> None:
        if session.deleted:
            return
        if len(question) + len(answer) > self.settings.max_history_chars:
            raise AppError(ErrorCode.BUDGET_EXCEEDED, "单轮历史超出内存预算。")
        session.history.append((question, answer))
        while (len(session.history) > self.settings.max_history_turns or
               sum(len(q) + len(a) for q, a in session.history) > self.settings.max_history_chars):
            session.history.pop(0)

    def add_citation(self, session: Session, citation: Citation) -> None:
        if session.deleted:
            raise AppError(ErrorCode.RUN_CANCELLED, "会话已清空。")
        existing = session.citations.get(citation.citation_id)
        if existing is not None and existing != citation:
            raise AppError(ErrorCode.CITATION_INVALID, "引用标识冲突。")
        if existing is None and len(session.citations) >= self.settings.max_citations:
            raise AppError(ErrorCode.BUDGET_EXCEEDED, "引用内存预算已用尽。")
        session.citations[citation.citation_id] = citation

    def add_chart(self, session: Session, png: bytes) -> str:
        if session.deleted:
            raise AppError(ErrorCode.RUN_CANCELLED, "会话已清空。")
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AppError(ErrorCode.DATA_NOT_READY, "计算模块没有返回 PNG。")
        if len(png) > self.settings.max_chart_bytes or len(session.charts) >= self.settings.max_charts:
            raise AppError(ErrorCode.BUDGET_EXCEEDED, "图表内存预算已用尽。")
        chart_id = opaque_id()
        session.charts[chart_id] = png
        return chart_id

    async def close(self) -> None:
        tasks = [s.task for s in self._sessions.values() if s.task is not None]
        for session in self._sessions.values():
            self._dispose(session)
        self._sessions.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)