"""Small, bounded client for the MTS agent API."""

import asyncio
import os
from typing import Any

import httpx


class MTSAPIError(Exception):
    """Safe error text to show to the voice agent."""


class MTSAPI:
    def __init__(self) -> None:
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
    ) -> Any:
        key = os.getenv("MTS_SUPER_ADMIN_KEY")
        if not key:
            raise MTSAPIError("The MTS API key is not configured")
        if not path.startswith("/api/agent/customers"):
            raise MTSAPIError("Unsupported MTS API path")

        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
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
                    raise MTSAPIError("MTS API could not be reached") from exc
                if (
                    response.status_code in {429, 502, 503, 504}
                    and attempt + 1 < attempts
                ):
                    await asyncio.sleep(0.3 * (2**attempt))
                    continue
                if response.is_redirect:
                    raise MTSAPIError("MTS API unexpectedly redirected the request")
                if response.is_error:
                    raise MTSAPIError(f"MTS API returned HTTP {response.status_code}")
                try:
                    return response.json()
                except ValueError as exc:
                    raise MTSAPIError("MTS API returned an invalid response") from exc
        raise MTSAPIError("MTS API request failed")


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
