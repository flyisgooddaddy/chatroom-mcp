"""Tests for push notifications."""

import httpx
import pytest

from chatroom.push import notify


class _MockTransport:
    def __init__(self, status_code=200):

        self.status_code = status_code

        self.calls = []

    def __call__(self, request):

        import httpx

        self.calls.append(request)

        return httpx.Response(self.status_code)


@pytest.mark.asyncio
async def test_notify_success(monkeypatch):

    t = _MockTransport(200)

    async def fake_post(self, url, json=None, **kw):

        return t(httpx.Request("POST", url, json=json or {}))

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    ok = await notify("http://x.test/hook", {"a": 1})

    assert ok is True

    assert len(t.calls) == 1


@pytest.mark.asyncio
async def test_notify_returns_false_on_4xx(monkeypatch):

    import httpx

    async def fake_post(self, url, json=None, **kw):

        return httpx.Response(404)

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    ok = await notify("http://x.test/hook", {"a": 1})

    assert ok is False


@pytest.mark.asyncio
async def test_notify_returns_false_on_network_error(monkeypatch):

    import httpx

    async def fake_post(self, url, json=None, **kw):

        raise httpx.ConnectError("nope")

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    ok = await notify("http://x.test/hook", {"a": 1}, retries=0)

    assert ok is False


@pytest.mark.asyncio
async def test_notify_retries(monkeypatch):

    import httpx

    call_count = {"n": 0}

    async def fake_post(self, url, json=None, **kw):

        call_count["n"] += 1

        if call_count["n"] < 2:
            raise httpx.ConnectError("nope")

        return httpx.Response(200)

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    ok = await notify("http://x.test/hook", {"a": 1}, retries=2)

    assert ok is True

    assert call_count["n"] == 2
