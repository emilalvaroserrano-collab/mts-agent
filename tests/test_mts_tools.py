"""Security and request-shape checks for the privileged MTS integration."""

from types import SimpleNamespace
from typing import Any

import pytest

import agent as agent_module
from agent import DefaultAgent, OperatorAgent
from mts_api import MTSAPIError


class FakeAPI:
    def __init__(self, customer_email: str = "alex@example.com") -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.customer_email = customer_email

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if method == "GET" and path.endswith("/42"):
            return {"id": 42, "email": self.customer_email}
        if path == "/api/agent/customers" and method == "GET":
            return [{"id": 42, "email": self.customer_email}]
        return {"ok": True}


@pytest.mark.asyncio
async def test_unbound_session_cannot_read_customer_data() -> None:
    agent = DefaultAgent()
    api = FakeAPI()
    agent.api = api
    result = await agent.get_me._func(agent)
    assert "error" in result
    with pytest.raises(MTSAPIError, match="No verified customer"):
        await agent.read_mts_page._func(agent, "orders")
    assert api.calls == []


@pytest.mark.asyncio
async def test_dispatch_email_must_match_api_profile() -> None:
    agent = DefaultAgent(42, "wrong@example.com")
    api = FakeAPI()
    agent.api = api
    with pytest.raises(MTSAPIError, match="did not match"):
        await agent.read_mts_page._func(agent, "tickets")
    assert api.calls == [("GET", "/api/agent/customers/42", {})]


@pytest.mark.asyncio
async def test_scoped_ticket_and_monthly_service_calls() -> None:
    agent = DefaultAgent(42, "alex@example.com")
    api = FakeAPI()
    agent.api = api
    with pytest.raises(MTSAPIError, match="Monthly charge confirmation"):
        await agent.order_mts_service._func(agent, "voip-ext")
    assert api.calls == []
    await agent.create_mts_ticket._func(agent, "Internet issue", "Office is offline")
    await agent.order_mts_service._func(
        agent, "voip-ext", confirm_monthly=True, recipient_email="alex@example.com"
    )
    assert api.calls[1] == (
        "POST",
        "/api/agent/customers/42/tickets",
        {"body": {"subject": "Internet issue", "body": "Office is offline"}},
    )
    assert api.calls[2][2]["body"] == {
        "offering": "voip-ext",
        "recipientEmail": "alex@example.com",
        "confirmMonthly": True,
    }


@pytest.mark.asyncio
async def test_operator_must_search_before_selecting_customer() -> None:
    agent = OperatorAgent(email="ops@example.com", operator=True)
    api = FakeAPI()
    agent.api = api
    with pytest.raises(MTSAPIError, match="Search"):
        await agent.select_mts_customer._func(agent, 42)
    await agent.find_mts_customers._func(agent, "alex@example.com")
    record = await agent.select_mts_customer._func(agent, 42)
    assert record["email"] == "alex@example.com"
    await agent.read_mts_page._func(agent, "billing")
    assert api.calls[-1][1] == "/api/agent/customers/42/billing"


@pytest.mark.asyncio
async def test_customer_delete_requires_frontend_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = OperatorAgent(email="ops@example.com", operator=True)
    api = FakeAPI()
    agent.api = api
    await agent.find_mts_customers._func(agent, "alex@example.com")
    await agent.select_mts_customer._func(agent, 42)
    monkeypatch.setattr(
        agent_module,
        "get_job_context",
        lambda: SimpleNamespace(room=SimpleNamespace(remote_participants={})),
    )
    with pytest.raises(MTSAPIError, match="requires staff confirmation"):
        await agent.delete_mts_customer._func(agent)
    assert not any(method == "DELETE" for method, _, _ in api.calls)
