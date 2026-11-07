import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    cli,
    function_tool,
    get_job_context,
    inference,
    llm,
    room_io,
)
from livekit.plugins import (
    ai_coustics,
)

from mts_api import MTSAPI, MTSAPIError, customer_list, customer_record

logger = logging.getLogger("agent-mts-agent")

load_dotenv(".env")
load_dotenv(".env.local")

CSR_SYSTEM_PROMPT = (
    Path(__file__).with_name("csr_system_prompt.md").read_text(encoding="utf-8")
)


def build_csr_instructions(
    customer_id: int | None, email: str | None, operator: bool
) -> str:
    """Combine the supplied CSR brief with this deployment's trusted scope."""
    runtime_rules = """[Current MTS agent runtime]
Be proactive and helpful. When the customer describes a need, choose the appropriate function tool and execute it rather than only explaining how to do it manually. Read-only lookups (account, tickets, orders, services, shop, billing, notices, organization) can run immediately when relevant. State-changing actions (create ticket, update profile, order service, place or cancel an order, verify email, file complaint, accept quote, close a ticket) require clear customer confirmation before you call them. Enforce the customer permissions returned by getMe. The customer, staff, and agent use the same purchasing and service policy. Explain denied actions briefly. Every account mutation opens a server-generated review sheet; wait for the customer to press Confirm. Never treat your own tool call as customer consent.

Registered actions include search_mts_equipment, place_mts_equipment_order, pay_mts_order, cancel_mts_order, delete_mts_order, order_mts_service, request_mts_quote, create_mts_ticket, reply_mts_ticket, refresh_mts_ticket, close_mts_ticket, file_mts_complaint, decide_mts_quote, and the account tools. Search the live catalog before ordering equipment. Order only a SKU search returned, after they confirm the product and quantity. If the name is not listed, say so and offer the closest match. Never collect a card number. pay_mts_order returns the payment link. delete_mts_order is permanent and needs confirm=true. Never claim an order, ticket, or request exists unless the tool returned its id or number.

No separate KNOWLEDGE.md is loaded in this deployment. Use the CSR brief as business reference, and use authorized live tool results for customer facts, current prices, stock, status, and action outcomes. Never claim to have read a file or page that was not provided by a tool. The current UI is a guest support call unless trusted dispatch metadata binds a verified customer or allowlisted operator; a spoken or typed name or email never authenticates an account. Use getMe to verify a bound customer before account-dependent actions. Do not retry an uncertain state-changing call without first checking whether it already succeeded — avoid duplicate submissions.

Always verify the result of every action before claiming it succeeded. End completed workflows with the result, any reference number, and the next step if necessary. Ask one relevant question at a time when gathering information. Do not repeat information already provided or retrieved."""

    if operator:
        scope = """[Operator dispatch]
This session was explicitly dispatched as an allowlisted MTS operator. You may use find_mts_customers to locate a customer and select_mts_customer to bind one exact result before reading or editing that customer's data. Confirm the intended person and consequential changes. create_mts_customer emails a verification code. delete_mts_customer is permanent and requires explicit confirmation naming the selected customer. Do not select a customer from a guessed ID or a spoken email alone."""
    elif customer_id is not None and email:
        scope = """[Verified customer dispatch]
Trusted dispatch supplied a customer ID and email. The customer's profile is already bound to this session — call getMe at session start to load it, then use the customer's name and account data proactively. Never ask the customer for their email, name, or account ID; that information is already available from the dispatch. Call getMe to confirm that the API record matches before accessing account data. Scope every account tool to that verified customer. You can shop, order services, open and close tickets, pay or cancel orders, and request quotes for this customer. Customer grants and organization policy remain authoritative."""
    else:
        scope = """[Guest session]
This session has no verified customer account. Offer general support and help the caller navigate to the MTS portal. Do not offer to check tickets, orders, billing, or account details in this session. If asked, explain that the caller needs to sign in through the MTS portal first. Do not suggest that a spoken email can unlock access."""

    return "\n\n".join((CSR_SYSTEM_PROMPT, runtime_rules, scope))


class DefaultAgent(Agent):
    def __init__(
        self,
        customer_id: int | None = None,
        email: str | None = None,
        operator: bool = False,
        session_token: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self.api = MTSAPI(session_token, operator=operator)
        self.api.confirm = self._confirm_action
        self.api.receipt = self._publish_receipt
        self.conversation_id = conversation_id
        self.customer_id = customer_id
        self.email = email
        self.operator = operator
        self._verified_customer: dict[str, Any] | None = None
        super().__init__(
            instructions=build_csr_instructions(customer_id, email, operator),
        )

    async def _account(self) -> int:
        if self.customer_id is None:
            raise MTSAPIError(
                "No verified customer is bound to this session. The portal must "
                "dispatch the agent with a customer ID and email. A spoken email "
                "alone cannot authorize account access."
            )
        if self._verified_customer is None:
            result = await self.api.request(
                "GET", f"/api/agent/customers/{self.customer_id}"
            )
            record = customer_record(result)
            if not self.operator and (
                not self.email
                or str(record.get("email", "")).casefold() != self.email.casefold()
            ):
                raise MTSAPIError("The dispatched customer identity did not match MTS")
            self._verified_customer = record
        return self.customer_id

    async def _path(self, suffix: str = "") -> str:
        customer_id = await self._account()
        return f"/api/agent/customers/{customer_id}{suffix}"

    @function_tool(name="getMe")
    async def get_me(self) -> dict[str, Any]:
        """Verify the account bound by trusted dispatch metadata before account actions."""
        if self.operator:
            return {
                "operator": True,
                "email": self.email,
                "customerId": self.customer_id,
            }
        try:
            await self._account()
        except MTSAPIError as exc:
            return {"error": str(exc)}
        return self._verified_customer or {}

    @function_tool()
    async def read_mts_page(
        self,
        page: Literal[
            "account",
            "pages",
            "home",
            "shop",
            "billing",
            "organization",
            "tickets",
            "orders",
            "services",
            "complaints",
            "notices",
        ],
    ) -> Any:
        """Read a page for the verified MTS customer; account is the profile."""
        suffix = "" if page == "account" else f"/{page}"
        return await self.api.request("GET", await self._path(suffix))

    @function_tool()
    async def search_mts_equipment(self, query: str) -> Any:
        """Search the live equipment catalog by name, SKU, or description. Call this before offering or ordering a product."""
        if not query.strip():
            raise MTSAPIError("Say what equipment to look for")
        result = await self.api.request(
            "GET", await self._path("/shop"), params={"q": query.strip()}
        )
        equipment = result.get("equipment") if isinstance(result, dict) else None
        if not isinstance(equipment, list):
            return result
        words = [word for word in query.lower().split() if len(word) > 2]
        ranked = []
        for item in equipment:
            if not isinstance(item, dict):
                continue
            haystack = " ".join(
                str(item.get(key, ""))
                for key in ("sku", "name", "category", "description")
            ).lower()
            score = sum(1 for word in words if word in haystack)
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        matches = [item for _, item in ranked[:5]]
        return {
            "query": query.strip(),
            "matches": matches,
            "canPurchase": result.get("canPurchase")
            if isinstance(result, dict)
            else None,
            "payLater": result.get("payLater") if isinstance(result, dict) else None,
        }

    @function_tool()
    async def place_mts_equipment_order(
        self,
        sku: str,
        quantity: int = 1,
        pay_later: bool = False,
        shipping: Literal["standard", "express"] = "standard",
        address: str | None = None,
        city: str | None = None,
        region: str | None = None,
        postal: str | None = None,
        notes: str | None = None,
        extra_items: str | None = None,
    ) -> Any:
        """Place confirmed catalog items for this customer. extra_items is 'SKU:qty, SKU:qty'."""
        if not sku.strip():
            raise MTSAPIError("A catalog SKU is required")
        if quantity < 1 or quantity > 99:
            raise MTSAPIError("Quantity must be from 1 to 99")
        items = [{"sku": sku.strip(), "quantity": quantity}]
        for piece in (extra_items or "").split(","):
            part = piece.strip()
            if not part:
                continue
            code, _, count = part.partition(":")
            extra_qty = int(count) if count.strip().isdigit() else 1
            if not code.strip():
                continue
            items.append({"sku": code.strip(), "quantity": extra_qty})
        payload: dict[str, Any] = {
            "items": items,
            "shippingMethod": shipping,
            "payLater": pay_later,
        }
        for key, value in {
            "address": address,
            "city": city,
            "region": region,
            "postal": postal,
            "notes": notes,
        }.items():
            if value:
                payload[key] = value
        return await self.api.request("POST", await self._path("/orders"), body=payload)

    @function_tool()
    async def pay_mts_order(self, order_id: int) -> Any:
        """Return the payment link for one of this customer's orders. Never ask for a card number."""
        if order_id <= 0:
            raise MTSAPIError("Order ID must be positive")
        return await self.api.request(
            "POST", await self._path(f"/orders/{order_id}/pay")
        )

    @function_tool()
    async def cancel_mts_order(self, order_id: int) -> Any:
        """Cancel one of this customer's orders after they confirm. Refuses an order that already shipped."""
        if order_id <= 0:
            raise MTSAPIError("Order ID must be positive")
        return await self.api.request(
            "POST", await self._path(f"/orders/{order_id}/cancel")
        )

    @function_tool()
    async def delete_mts_order(self, order_id: int, confirm: bool = False) -> Any:
        """Permanently delete one of this customer's orders. Requires confirm true. This operation is unavailable in customer support."""
        if order_id <= 0:
            raise MTSAPIError("Order ID must be positive")
        if not confirm:
            raise MTSAPIError(
                "Ask the customer to confirm the delete before calling this"
            )
        return await self.api.request(
            "POST",
            await self._path(f"/orders/{order_id}/delete"),
            body={"confirm": True},
        )

    @function_tool()
    async def refresh_mts_ticket(self, ticket_id: int) -> Any:
        """Pull the latest Zendesk status and public comments for one of this customer's tickets."""
        if ticket_id <= 0:
            raise MTSAPIError("Ticket ID must be positive")
        return await self.api.request(
            "POST", await self._path(f"/tickets/{ticket_id}/refresh")
        )

    @function_tool()
    async def close_mts_ticket(self, ticket_id: int) -> Any:
        """Close one of this customer's tickets after they confirm."""
        if ticket_id <= 0:
            raise MTSAPIError("Ticket ID must be positive")
        return await self.api.request(
            "POST", await self._path(f"/tickets/{ticket_id}/close")
        )

    @function_tool()
    async def request_mts_quote(self, service_name: str, details: str) -> Any:
        """Request a quote for a catalog IT or phone service after the customer describes the work."""
        if not service_name.strip() or not details.strip():
            raise MTSAPIError("A service name and the details are required")
        return await self.api.request(
            "POST",
            await self._path("/services/quote"),
            body={"name": service_name.strip(), "details": details.strip()},
        )

    @function_tool()
    async def update_mts_profile(
        self,
        name: str | None = None,
        phone: str | None = None,
        business: str | None = None,
        address: str | None = None,
        city: str | None = None,
        region: str | None = None,
        postal: str | None = None,
        country: str | None = None,
    ) -> Any:
        """Update the verified customer's profile after explicit confirmation."""
        fields = locals().copy()
        fields.pop("self")
        body = {key: value for key, value in fields.items() if value is not None}
        if not body:
            raise MTSAPIError("At least one profile field is required")
        result = await self.api.request("PATCH", await self._path(), body=body)
        self._verified_customer = None
        return result

    @function_tool()
    async def verify_mts_email(self, code: str) -> Any:
        """Submit the verification code read by the customer from their email."""
        return await self.api.request(
            "POST", await self._path("/verify"), body={"code": code}
        )

    @function_tool()
    async def resend_mts_verification(self) -> Any:
        """Send a new customer verification email; the API never returns the code."""
        return await self.api.request("POST", await self._path("/verify/resend"))

    @function_tool()
    async def save_mts_organization(
        self, business: str, phone: str | None = None, city: str | None = None
    ) -> Any:
        """Save organization details and open a review ticket after confirmation."""
        body = {"business": business}
        if phone is not None:
            body["phone"] = phone
        if city is not None:
            body["city"] = city
        return await self.api.request(
            "POST", await self._path("/organization"), body=body
        )

    @function_tool()
    async def read_mts_record(
        self, kind: Literal["ticket", "order"], record_id: int
    ) -> Any:
        """Read one ticket with comments or one order with payment and tracking."""
        if record_id <= 0:
            raise MTSAPIError("Record ID must be positive")
        segment = "tickets" if kind == "ticket" else "orders"
        return await self.api.request(
            "GET", await self._path(f"/{segment}/{record_id}")
        )

    @function_tool()
    async def create_mts_ticket(
        self,
        subject: str,
        body: str,
        category: str | None = None,
        severity: str | None = None,
        device: str | None = None,
    ) -> Any:
        """Open a support ticket after confirming its subject and description."""
        payload = {"subject": subject, "body": body}
        payload.update(
            {
                k: v
                for k, v in {
                    "category": category,
                    "severity": severity,
                    "device": device,
                }.items()
                if v is not None
            }
        )
        return await self.api.request(
            "POST", await self._path("/tickets"), body=payload
        )

    @function_tool()
    async def reply_mts_ticket(self, ticket_id: int, body: str) -> Any:
        """Post a public reply to a verified customer's ticket after confirmation."""
        if ticket_id <= 0:
            raise MTSAPIError("Ticket ID must be positive")
        return await self.api.request(
            "POST",
            await self._path(f"/tickets/{ticket_id}/replies"),
            body={"body": body},
        )

    @function_tool()
    async def order_mts_service(
        self,
        offering: Literal[
            "network",
            "voip-line",
            "voip-ext",
            "fax-physical",
            "fax-efax",
            "internet-spectrum",
            "internet-att",
            "internet-frontier",
            "internet-fixed",
            "wiring",
        ],
        site: str | None = None,
        notes: str | None = None,
        recipient_email: str | None = None,
        drops: int | None = None,
        ship_phone: bool | None = None,
        quantity: int = 1,
        site_id: str | None = None,
        assigned_users: str | None = None,
        handset_sku: str | None = None,
        confirm_monthly: bool = False,
    ) -> Any:
        """Order a service after confirming terms; voip-ext needs monthly consent."""
        if offering == "voip-ext" and not confirm_monthly:
            raise MTSAPIError("Monthly charge confirmation is required for voip-ext")
        if not 1 <= quantity <= 99:
            raise MTSAPIError("Quantity must be from 1 to 99")
        payload: dict[str, Any] = {"offering": offering, "quantity": quantity}
        for key, value in {
            "site": site,
            "notes": notes,
            "recipientEmail": recipient_email,
            "drops": drops,
            "shipPhone": ship_phone,
            "siteId": site_id,
            "assignedUsers": assigned_users,
            "handsetSku": handset_sku,
        }.items():
            if value is not None:
                payload[key] = value
        if confirm_monthly:
            payload["confirmMonthly"] = True
        return await self.api.request(
            "POST", await self._path("/services"), body=payload
        )

    @function_tool()
    async def decide_mts_quote(
        self, request_id: int, decision: Literal["accepted", "rejected"]
    ) -> Any:
        """Accept or reject a quoted service request after explicit confirmation."""
        if request_id <= 0:
            raise MTSAPIError("Request ID must be positive")
        return await self.api.request(
            "POST",
            await self._path(f"/quotes/{request_id}"),
            body={"decision": decision},
        )

    @function_tool()
    async def file_mts_complaint(
        self, subject: str, body: str, kind: str | None = None
    ) -> Any:
        """File a complaint and linked ticket after confirming its details."""
        payload = {"subject": subject, "body": body}
        if kind is not None:
            payload["kind"] = kind
        return await self.api.request(
            "POST", await self._path("/complaints"), body=payload
        )

    @function_tool()
    async def mark_mts_notice_read(self, notice_id: int) -> Any:
        """Mark one notice for the verified customer as read."""
        if notice_id <= 0:
            raise MTSAPIError("Notice ID must be positive")
        return await self.api.request(
            "POST", await self._path(f"/notices/{notice_id}/read")
        )

    @function_tool()
    async def open_mts_website(
        self,
        page: Literal[
            "home",
            "shop",
            "portal",
            "tickets",
            "orders",
            "billing",
            "account",
            "organization",
            "notifications",
            "my-services",
        ],
    ) -> Any:
        """Prepare an MTS website link in the caller's browser panel."""
        paths = {
            "home": "/",
            "shop": "/portal/shop",
            "portal": "/portal",
            "tickets": "/portal/tickets",
            "orders": "/portal/orders",
            "billing": "/portal/billing",
            "account": "/portal/account",
            "organization": "/portal/organization",
            "notifications": "/portal/notifications",
            "my-services": "/portal/my-services",
        }
        room = get_job_context().room
        frontend = next(
            (
                identity
                for identity in room.remote_participants
                if identity.startswith(("csr-", "visitor-"))
            ),
            None,
        )
        if frontend is None:
            raise MTSAPIError("No support browser is connected")
        try:
            response = await room.local_participant.perform_rpc(
                destination_identity=frontend,
                method="mts.openWebsite",
                payload=json.dumps({"path": paths[page]}),
                response_timeout=5.0,
            )
        except Exception as exc:
            raise MTSAPIError("The support browser did not accept the link") from exc
        return {
            "browser": json.loads(response),
            "note": "Report navigated only when the browser confirms it. Otherwise a link is prepared.",
        }

    async def _confirm_action(self, action: dict[str, Any]) -> bool:
        room = get_job_context().room
        frontend = next(
            (
                identity
                for identity in room.remote_participants
                if identity.startswith(("csr-", "visitor-"))
            ),
            None,
        )
        if frontend is None:
            raise MTSAPIError(
                "A connected support screen is required to review this action"
            )
        try:
            result = await room.local_participant.perform_rpc(
                destination_identity=frontend,
                method="mts.confirmAction",
                payload=json.dumps(action),
                response_timeout=120.0,
            )
            return json.loads(result).get("confirmed") is True
        except Exception as exc:
            raise MTSAPIError(
                "This action was not confirmed. Nothing was submitted."
            ) from exc

    async def _publish_receipt(self, receipt: dict[str, Any]) -> None:
        room = get_job_context().room
        frontend = next(
            (
                identity
                for identity in room.remote_participants
                if identity.startswith("csr-")
            ),
            None,
        )
        if frontend:
            await room.local_participant.perform_rpc(
                destination_identity=frontend,
                method="mts.actionReceipt",
                payload=json.dumps(receipt),
                response_timeout=5.0,
            )

    @function_tool()
    async def search_mts_knowledge(self, query: str) -> Any:
        """Search approved support guides and policy for this customer's organization."""
        return await self.api.request(
            "GET", await self._path("/knowledge"), params={"q": query}
        )

    @function_tool()
    async def read_mts_context(self) -> Any:
        """Read the customer's sites, contacts, support agreement, policy, and service activation state."""
        result = await self.api.request("GET", await self._path("/context"))
        room = get_job_context().room
        participant = next(
            (
                p
                for identity, p in room.remote_participants.items()
                if identity.startswith("csr-")
            ),
            None,
        )
        if isinstance(result, dict):
            result["currentPage"] = (
                participant.attributes.get("mts.page", "/portal")
                if participant
                else "/portal"
            )
        return result

    @function_tool()
    async def read_mts_ledger(self) -> Any:
        """Read synchronized invoices, due dates, balances, and quoted or active recurring service charges. A missing billing feed means balances are unknown."""
        return await self.api.request("GET", await self._path("/ledger"))

    @function_tool()
    async def read_mts_action_status(self, action_id: str) -> Any:
        """Reconcile a confirmed request after an uncertain response. Never repeat a manual-review or executing action."""
        return await self.api.request(
            "GET", await self._path(f"/confirmations/{action_id}")
        )

    @function_tool()
    async def escalate_mts_ticket(self, ticket_id: int, reason: str) -> Any:
        """Hand the existing ticket to the human support desk with the issue and steps already tried."""
        if ticket_id <= 0 or not reason.strip():
            raise MTSAPIError("A ticket and escalation summary are required")
        return await self.api.request(
            "POST",
            await self._path(f"/tickets/{ticket_id}/escalate"),
            body={"reason": reason[:2000]},
        )

    async def on_enter(self):
        profile = (
            await self.get_me() if self.customer_id is not None and self.email else None
        )
        if self.conversation_id and profile and not profile.get("error"):
            try:
                messages = await self.api.request(
                    "GET",
                    await self._path(f"/conversations/{self.conversation_id}/messages"),
                )
                context = llm.ChatContext()
                for message in messages[-20:] if isinstance(messages, list) else []:
                    if message.get("role") in {"user", "assistant"}:
                        context.add_message(
                            role=message["role"], content=str(message.get("body", ""))
                        )
                await self.update_chat_ctx(context)
            except MTSAPIError:
                logger.warning("Conversation history could not be loaded")
        if self.operator:
            greeting = "Say: Hi, this is Sarah. What can I help you with?"
        elif profile and not profile.get("error"):
            name = (profile.get("context") or {}).get("profile") or {}
            name = name.get("preferred_name") or profile.get("name") or ""
            greeting = f"Greet {name} naturally in one short sentence and ask what they need. If earlier messages show an unresolved issue, refer to it briefly. Do not list capabilities or repeat an introduction."
        else:
            greeting = "Say: Hi, this is Sarah at Manage Tech Services. How can I help? Account work requires a linked portal session; mention that only when relevant."
        await self.session.generate_reply(
            instructions=greeting, allow_interruptions=True
        )


class OperatorAgent(DefaultAgent):
    """Extra customer management tools for an allowlisted operator dispatch."""

    @function_tool(name="find_mts_customers")
    async def find_mts_customers(self, q: str) -> Any:
        """Find customers by name, email, or company for an authorized operator."""
        if not q.strip():
            raise MTSAPIError("A search term is required")
        result = await self.api.request(
            "GET", "/api/agent/customers", params={"q": q.strip()}
        )
        self._found_customer_ids = {
            item.get("id")
            for item in customer_list(result)
            if isinstance(item.get("id"), int)
        }
        return result

    @function_tool()
    async def select_mts_customer(self, customer_id: int) -> Any:
        """Select exactly one customer returned by the latest operator search."""
        if customer_id not in getattr(self, "_found_customer_ids", set()):
            raise MTSAPIError("Search for this customer before selecting them")
        self.customer_id = customer_id
        self._verified_customer = None
        await self._account()
        return self._verified_customer or {}

    @function_tool()
    async def create_mts_customer(
        self,
        email: str,
        name: str | None = None,
        phone: str | None = None,
        business: str | None = None,
        address: str | None = None,
        city: str | None = None,
        region: str | None = None,
        postal: str | None = None,
        country: str | None = None,
    ) -> Any:
        """Create a customer after confirming the email and profile details."""
        if not email.strip():
            raise MTSAPIError("Email is required")
        payload = {"email": email.strip()}
        for key, value in {
            "name": name,
            "phone": phone,
            "business": business,
            "address": address,
            "city": city,
            "region": region,
            "postal": postal,
            "country": country,
        }.items():
            if value is not None:
                payload[key] = value
        return await self.api.request("POST", "/api/agent/customers", body=payload)

    @function_tool()
    async def delete_mts_customer(self) -> Any:
        """Permanently delete the selected customer after explicit confirmation."""
        path = await self._path()
        email = str((self._verified_customer or {}).get("email", ""))
        if not email:
            raise MTSAPIError("The selected customer email could not be verified")
        room = get_job_context().room
        frontend = next(
            (
                identity
                for identity in room.remote_participants
                if identity.startswith("csr-")
            ),
            None,
        )
        if frontend is None:
            raise MTSAPIError(
                "Customer deletion requires staff confirmation in the console"
            )
        try:
            response = await room.local_participant.perform_rpc(
                destination_identity=frontend,
                method="mts.confirmDelete",
                payload=json.dumps({"customerId": self.customer_id, "email": email}),
                response_timeout=30.0,
            )
            confirmed = json.loads(response).get("confirmed") is True
        except Exception as exc:
            raise MTSAPIError("Customer deletion was not confirmed") from exc
        if not confirmed:
            raise MTSAPIError("Customer deletion was cancelled")
        result = await self.api.request("DELETE", path)
        self.customer_id = None
        self._verified_customer = None
        return result


server = AgentServer()


@server.rtc_session(agent_name="mts-agent-ai")
async def entrypoint(ctx: JobContext):
    try:
        metadata = json.loads(ctx.job.metadata or "{}")
        if not isinstance(metadata, dict):
            metadata = {}
    except ValueError:
        metadata = {}
    raw_customer_id = metadata.get("mts_customer_id")
    customer_id = (
        raw_customer_id
        if type(raw_customer_id) is int and raw_customer_id > 0
        else None
    )
    email = metadata.get("email") if isinstance(metadata.get("email"), str) else None
    allowlist = {
        item.strip().casefold()
        for item in os.getenv("MTS_OPERATOR_EMAIL_ALLOWLIST", "").split(",")
        if item.strip()
    }
    operator = bool(
        metadata.get("mts_operator") is True and email and email.casefold() in allowlist
    )
    agent = (OperatorAgent if operator else DefaultAgent)(
        customer_id,
        email,
        operator,
        metadata.get("mts_session_token"),
        metadata.get("conversation_id"),
    )
    session = AgentSession(
        llm=inference.RealtimeModel(
            model=os.getenv("CSR_REALTIME_MODEL", "google/gemini-live"),
            voice=os.getenv("CSR_VOICE", "Aoede"),
        ),
    )

    pending: set[asyncio.Task[Any]] = set()

    async def persist(item: Any) -> None:
        content = getattr(item, "text_content", None)
        role = getattr(item, "role", None)
        if (
            not agent.conversation_id
            or role not in {"user", "assistant"}
            or not content
            or "[[mts-" in content
        ):
            return
        try:
            await agent.api.request(
                "POST",
                await agent._path(f"/conversations/{agent.conversation_id}/messages"),
                body={"externalId": item.id, "role": role, "body": content},
            )
        except MTSAPIError:
            logger.exception("Conversation persistence failed")

    @session.on("conversation_item_added")
    def conversation_item_added(event: Any) -> None:
        task = asyncio.create_task(persist(event.item))
        pending.add(task)
        task.add_done_callback(pending.discard)

    async def flush_history() -> None:
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    ctx.add_shutdown_callback(flush_history)

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_S,
                ),
            ),
        ),
    )

    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(
            BuiltinAudioClip.OFFICE_AMBIENCE,
            volume=max(0.0, min(1.0, float(os.getenv("CSR_AMBIENCE_VOLUME", "0.12")))),
        ),
    )

    await background_audio.start(room=ctx.room, agent_session=session)


if __name__ == "__main__":
    cli.run_app(server)
