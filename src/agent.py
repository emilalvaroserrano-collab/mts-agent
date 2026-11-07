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
Be proactive and helpful. When the customer describes a need, choose the appropriate function tool and execute it rather than only explaining how to do it manually. Read-only lookups (account, tickets, orders, services, shop, billing, notices, organization) can run immediately when relevant. State-changing actions (create ticket, update profile, order service, verify email, file complaint, accept quote) require clear customer confirmation before you call them.

Only the function tools actually registered in this session can execute actions. Names such as show_page, list_tickets, find_equipment, and checkout are not callable unless registered. This agent has no shopping cart, checkout, or payment processing tool, and cannot open a browser or click links on the caller's behalf. Never claim to have completed an action that a tool did not perform. open_mts_website only prepares an approved link in the caller's browser panel; call it before saying a link is ready, and if no support browser is connected, explain the limitation and offer https://mts.192-888-702-801.site/ as a manual destination.

No separate KNOWLEDGE.md is loaded in this deployment. Use the CSR brief as business reference, and use authorized live tool results for customer facts, current prices, stock, status, and action outcomes. Never claim to have read a file or page that was not provided by a tool. The current UI is a guest support call unless trusted dispatch metadata binds a verified customer or allowlisted operator; a spoken or typed name or email never authenticates an account. Use getMe to verify a bound customer before account-dependent actions. Do not retry an uncertain state-changing call without first checking whether it already succeeded — avoid duplicate submissions.

Always verify the result of every action before claiming it succeeded. End completed workflows with the result, any reference number, and the next step if necessary. Ask one relevant question at a time when gathering information. Do not repeat information already provided or retrieved."""

    if operator:
        scope = """[Operator dispatch]
This session was explicitly dispatched as an allowlisted MTS operator. You may use find_mts_customers to locate a customer and select_mts_customer to bind one exact result before reading or editing that customer's data. Confirm the intended person and consequential changes. create_mts_customer emails a verification code. delete_mts_customer is permanent and requires explicit confirmation naming the selected customer. Do not select a customer from a guessed ID or a spoken email alone."""
    elif customer_id is not None and email:
        scope = """[Verified customer dispatch]
Trusted dispatch supplied a customer ID and email. The customer's profile is already bound to this session — call getMe at session start to load it, then use the customer's name and account data proactively. Never ask the customer for their email, name, or account ID; that information is already available from the dispatch. Call getMe to confirm that the API record matches before accessing account data. Scope every account tool to that verified customer. Do not offer unsupported checkout or payment actions."""
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
    ) -> None:
        self.api = MTSAPI()
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
        confirm_monthly: bool = False,
    ) -> Any:
        """Order a service after confirming terms; voip-ext needs monthly consent."""
        if offering == "voip-ext" and not confirm_monthly:
            raise MTSAPIError("Monthly charge confirmation is required for voip-ext")
        payload: dict[str, Any] = {"offering": offering}
        for key, value in {
            "site": site,
            "notes": notes,
            "recipientEmail": recipient_email,
            "drops": drops,
            "shipPhone": ship_phone,
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
        page: Literal["home", "shop", "portal", "tickets", "orders", "billing"],
    ) -> Any:
        """Prepare an MTS website link in the caller's browser panel."""
        paths = {
            "home": "/",
            "shop": "/portal/shop",
            "portal": "/portal",
            "tickets": "/portal/tickets",
            "orders": "/portal/orders",
            "billing": "/portal/billing",
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
        return {"browser": response, "note": "A link is ready for the visitor to open"}

    async def on_enter(self):
        # Proactively load the bound customer profile so Sarah never asks
        # for information already available from trusted dispatch.
        profile = None
        if self.customer_id is not None and self.email:
            profile = await self.get_me()

        if self.operator:
            greeting = "Welcome to Manage Tech Services. I'm Sarah, your support agent. I can look up customers, create accounts, manage tickets, orders, services, and billing. What would you like to do first?"
        elif (
            profile
            and not profile.get("error")
            and self.customer_id is not None
            and self.email
        ):
            name = profile.get("name") or str(profile.get("email", "")).split("@")[0]
            greeting = f"Welcome back, {name}. I'm Sarah, your support agent. Your account is linked to this session. I can check your tickets and orders, update your profile, order services, file complaints, read notices, and more. What can I help you with today?"
        elif self.customer_id is not None and self.email:
            greeting = "Welcome to Manage Tech Services. I'm Sarah, your support agent. Your account is linked to this session. I can check your tickets and orders, update your profile, order services, file complaints, read notices, and more. What can I help you with today?"
        else:
            greeting = "Welcome to Manage Tech Services. I'm Sarah, your support agent. This is a guest session. I can answer general questions and point you to the right MTS pages. If you need to check tickets, orders, billing, or your account, please sign in through the MTS portal first. What would you like help with?"
        await self.session.generate_reply(
            instructions=greeting,
            allow_interruptions=True,
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


@server.rtc_session(agent_name="mts-agent")
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
    session = AgentSession(
        llm=inference.RealtimeModel(
            model="xai/grok-voice-3.0",
            voice="carina",
        ),
    )

    await session.start(
        agent=(OperatorAgent if operator else DefaultAgent)(
            customer_id, email, operator
        ),
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
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=1.0),
    )

    await background_audio.start(room=ctx.room, agent_session=session)


if __name__ == "__main__":
    cli.run_app(server)
