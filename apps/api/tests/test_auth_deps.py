from __future__ import annotations

import pytest

from noa_api.core.auth import deps as auth_deps


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _FakeSessionContextManager:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return False


@pytest.mark.asyncio
async def test_get_auth_service_commits_on_pending_approval_http_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()

    def _fake_session_factory():
        def _maker() -> _FakeSessionContextManager:
            return _FakeSessionContextManager(session)

        return _maker

    monkeypatch.setattr(auth_deps, "get_session_factory", _fake_session_factory)

    # Import locally so this test doesn't create a circular import in app code.
    from noa_api.api.error_handling import ApiHTTPException

    agen = auth_deps.get_auth_service()
    await anext(agen)

    exc = ApiHTTPException(
        status_code=403,
        detail="User pending approval",
        error_code="user_pending_approval",
    )
    with pytest.raises(ApiHTTPException):
        await agen.athrow(exc)

    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_get_auth_service_commits_on_raw_pending_approval_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AuthPendingApprovalError carries error_code and triggers commit (V56)."""
    session = _FakeSession()

    def _fake_session_factory():
        def _maker() -> _FakeSessionContextManager:
            return _FakeSessionContextManager(session)

        return _maker

    monkeypatch.setattr(auth_deps, "get_session_factory", _fake_session_factory)

    from noa_api.core.auth.errors import AuthPendingApprovalError

    agen = auth_deps.get_auth_service()
    await anext(agen)

    exc = AuthPendingApprovalError("User pending approval")
    with pytest.raises(AuthPendingApprovalError):
        await agen.athrow(exc)

    assert session.commits == 1
    assert session.rollbacks == 0


@pytest.mark.asyncio
async def test_get_auth_service_rolls_back_on_other_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()

    def _fake_session_factory():
        def _maker() -> _FakeSessionContextManager:
            return _FakeSessionContextManager(session)

        return _maker

    monkeypatch.setattr(auth_deps, "get_session_factory", _fake_session_factory)

    agen = auth_deps.get_auth_service()
    await anext(agen)

    with pytest.raises(RuntimeError, match="boom"):
        await agen.athrow(RuntimeError("boom"))

    assert session.commits == 0
    assert session.rollbacks == 1
