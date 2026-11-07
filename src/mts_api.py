"""Small, bounded client for the MTS agent API."""

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import httpx


class MTSAPIError(Exception):
    """Safe error text to show to the voice agent."""


class MTSAPI:
    def __init__(
        self, session_token: str | None = None, *, operator: bool = False
    ) -> None:
        self.session_token = session_token
        self.operator = operator
        self.confirm: Callable[[dict[str, Any]], Awaitable[bool]] | None = None
        self.receipt: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self.base_url = os.getenv(
            "MTS_API_BASE_URL", "https://mts.192-888-702-801.site"
        ).rstrip("/")
        if not self.base_url.startswith("https://"):
            raise MTSAPIError("MTS_API_BASE_URL must use HTTPS")

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        action_id: str | None = None,
    ) -> Any:
        key = self.session_token or (
            os.getenv("MTS_OPERATOR_KEY") or os.getenv("MTS_SUPER_ADMIN_KEY")
            if self.operator
            else None
        )
        if not key:
            raise MTSAPIError("A portal-authenticated support session is required")
        if not re.fullmatch(r"/api/agent/customers(?:/[A-Za-z0-9_-]+)*", path):
            raise MTSAPIError("Unsupported MTS API path")

        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        match = re.match(r"^(/api/agent/customers/\d+)(?:/(.*))?$", path)
        resource = (match.group(2) or "") if match else ""
        if match and needs_confirmation(method, resource) and action_id is None:
            if self.confirm is None:
                raise MTSAPIError("The support screen must confirm this action")
            prepared = await self.request(
                "POST",
                f"{match.group(1)}/confirmations",
                body={"method": method, "path": path, "body": body or {}},
            )
            if not isinstance(prepared, dict) or (
                prepared.get("status") != "approved"
                and not await self.confirm(prepared)
            ):
                raise MTSAPIError(
                    "The customer cancelled or did not confirm this action"
                )
            action_id = str(prepared["id"])
        if action_id:
            headers["X-MTS-Action-Id"] = action_id
        timeout = httpx.Timeout(10.0, connect=5.0)
        attempts = 3 if method == "GET" else 1
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, follow_redirects=False
        ) as client:
            for attempt in range(attempts):
                try:
                    response = await client.request(
                        method, path, headers=headers, params=params, json=body
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(0.3 * (2**attempt))
                        continue
                    detail = (
                        f" Action {action_id} has an uncertain result; check its status before resubmitting."
                        if action_id
                        else ""
                    )
                    raise MTSAPIError("MTS API could not be reached." + detail) from exc
                if (
                    response.status_code in {429, 502, 503, 504}
                    and attempt + 1 < attempts
                ):
                    await asyncio.sleep(0.3 * (2**attempt))
                    continue
                if response.is_redirect:
                    raise MTSAPIError("MTS API unexpectedly redirected the request")
                if response.is_error:
                    try:
                        message = response.json().get("error", "")
                    except (ValueError, AttributeError):
                        message = ""
                    raise MTSAPIError(
                        str(message)[:300]
                        or f"MTS API returned HTTP {response.status_code}"
                    )
                try:
                    result = response.json()
                    if action_id and self.receipt and isinstance(result, dict):
                        with suppress(Exception):
                            await self.receipt(result)
                    return result
                except ValueError as exc:
                    raise MTSAPIError("MTS API returned an invalid response") from exc
        raise MTSAPIError("MTS API request failed")


def needs_confirmation(method: str, resource: str) -> bool:
    if method in {"GET", "OPTIONS"}:
        return False
    return not re.match(
        r"^(confirmations|conversations|push|notices|verify)(/|$)", resource
    ) and not re.fullmatch(r"tickets/\d+/refresh", resource)


def customer_record(result: Any) -> dict[str, Any]:
    """Unwrap common API response envelopes without assuming a customer exists."""
    if isinstance(result, dict):
        if isinstance(result.get("customer"), dict):
            return result["customer"]
        if isinstance(result.get("data"), dict):
            return result["data"]
        return result
    return {}


def customer_list(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        for key in ("customers", "results", "data"):
            if isinstance(result.get(key), list):
                return [item for item in result[key] if isinstance(item, dict)]
    return []
