import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { AccessToken, RoomAgentDispatch, RoomConfiguration } from "livekit-server-sdk";

export const runtime = "nodejs";

type Handoff = {
  id: number;
  email: string;
  name: string;
  phone: string;
  business: string;
  organization: string;
  role: string;
  agentToken: string;
};

const PORTAL = (process.env.MTS_API_BASE_URL || "https://mts.192-888-702-801.site").replace(/\/$/, "");

async function redeem(code: string): Promise<Handoff | null> {
  if (!code) return null;
  const response = await fetch(`${PORTAL}/api/agent/handoff/redeem`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
    cache: "no-store",
  });
  if (!response.ok) return null;
  const data = (await response.json()) as Partial<Handoff>;
  if (typeof data.id !== "number" || data.id <= 0 || typeof data.email !== "string" || !data.email || typeof data.agentToken !== "string" || !data.agentToken.startsWith("csr_")) return null;
  return {
    id: data.id,
    email: data.email,
    name: typeof data.name === "string" ? data.name : "",
    phone: typeof data.phone === "string" ? data.phone : "",
    business: typeof data.business === "string" ? data.business : "",
    organization: typeof data.organization === "string" ? data.organization : "",
    role: typeof data.role === "string" ? data.role : "",
    agentToken: data.agentToken,
  };
}

export async function POST(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) {
    return NextResponse.json({ error: "Invalid request origin" }, { status: 403 });
  }
  if (!process.env.LIVEKIT_URL || !process.env.LIVEKIT_API_KEY || !process.env.LIVEKIT_API_SECRET) {
    return NextResponse.json({ error: "Support is not configured" }, { status: 503 });
  }

  const body = (await request.json().catch(() => ({}))) as { code?: unknown };
  const code = typeof body.code === "string" ? body.code.slice(0, 80) : "";
  let customer: Handoff | null;
  try { customer = await redeem(code); } catch { return NextResponse.json({ error: "The portal could not be reached. Please retry." }, { status: 503 }); }
  if(code && !customer) return NextResponse.json({error:"Your portal handoff expired. Reopen support from the portal."},{status:401});
  if (!customer && process.env.CSR_ENABLE_GUEST_ACCESS !== "true") {
    return NextResponse.json({ error: "Sign in through the MTS portal to reach support." }, { status: 403 });
  }

  let conversationId = "";
  let previousMessages: unknown[] = [];
  if(customer) {
    try {
      const api = async (resource: string, method = "GET") => {
        const response = await fetch(`${PORTAL}/api/agent/customers/${customer!.id}/${resource}`, { method, headers:{Authorization:`Bearer ${customer!.agentToken}`,"Content-Type":"application/json"},cache:"no-store",signal:AbortSignal.timeout(15000) });
        if(!response.ok) throw new Error("Support history is unavailable");
        return response.json();
      };
      const conversations = await api("conversations");
      if(Array.isArray(conversations)&&conversations[0]?.status==="open") conversationId=conversations[0].id;
      else conversationId=(await api("conversations","POST")).id;
      previousMessages=await api(`conversations/${conversationId}/messages`);
    }catch{return NextResponse.json({error:"Support history could not be initialized. Please retry."},{status:503});}
  }
  const room = `mts-support-${randomUUID()}`;
  const token = new AccessToken(process.env.LIVEKIT_API_KEY, process.env.LIVEKIT_API_SECRET, {
    identity: customer ? `csr-${customer.id}` : `visitor-${randomUUID()}`,
    name: customer?.name || "MTS visitor",
    ttl: "10m",
  });
  token.addGrant({ roomJoin: true, room, canPublish: true, canPublishData: true, canSubscribe: true });
  token.roomConfig = new RoomConfiguration({
    agents: [new RoomAgentDispatch({
      agentName: "mts-agent-ai",
      metadata: JSON.stringify(customer
        ? {
            mts_customer_id: customer.id,
            email: customer.email,
            name: customer.name,
            phone: customer.phone,
            business: customer.business,
            organization: customer.organization,
            role: customer.role,
            mts_session_token: customer.agentToken,
            conversation_id: conversationId,
          }
        : { guest: true }),
    })],
  });

  return NextResponse.json(
    { server_url: process.env.LIVEKIT_URL, participant_token: await token.toJwt(), support: customer ? {customerId:customer.id,token:customer.agentToken,conversationId,previousMessages} : null },
    { status: 201, headers: { "Cache-Control": "no-store" } },
  );
}
