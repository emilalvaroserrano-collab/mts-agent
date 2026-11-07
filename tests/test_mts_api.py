import httpx
import pytest

import mts_api
from mts_api import MTSAPI, MTSAPIError


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        mts_api.httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_customer_never_falls_back_to_operator_key(monkeypatch):
    monkeypatch.setenv("MTS_SUPER_ADMIN_KEY", "staff-only")
    with pytest.raises(MTSAPIError, match="authenticated"):
        await MTSAPI().request("GET", "/api/agent/customers/42")


@pytest.mark.asyncio
async def test_reviewed_write_binds_exact_action_and_returns_receipt(monkeypatch):
    calls = []
    receipts = []

    def handle(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer csr_own-session"
        return (
            httpx.Response(201, json={"id": "action-1", "title": "Create ticket"})
            if request.url.path.endswith("/confirmations")
            else httpx.Response(200, json={"ticketId": 123})
        )

    transport(monkeypatch, handle)
    api = MTSAPI("csr_own-session")

    async def confirm(action):
        return action["id"] == "action-1"

    async def receipt(result):
        receipts.append(result)

    api.confirm, api.receipt = confirm, receipt
    assert await api.request(
        "POST", "/api/agent/customers/42/tickets", body={"subject": "Internet"}
    ) == {"ticketId": 123}
    assert len(calls) == 2
    assert calls[-1].headers["X-MTS-Action-Id"] == "action-1"
    assert receipts == [{"ticketId": 123}]


@pytest.mark.asyncio
async def test_declined_review_never_executes_the_write(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(201, json={"id": "action-1"})

    transport(monkeypatch, handle)
    api = MTSAPI("csr_own-session")

    async def decline(_action):
        return False

    api.confirm = decline
    with pytest.raises(MTSAPIError, match="cancelled"):
        await api.request("POST", "/api/agent/customers/42/orders", body={"items": []})
    assert calls == ["/api/agent/customers/42/confirmations"]


@pytest.mark.asyncio
async def test_writes_are_not_retried_after_timeout(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout("uncertain", request=request)

    transport(monkeypatch, handle)
    with pytest.raises(MTSAPIError):
        await MTSAPI("csr_own-session").request(
            "POST",
            "/api/agent/customers/42/tickets",
            body={},
            action_id="approved-action",
        )
    assert len(calls) == 1
