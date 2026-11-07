import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { AccessToken, RoomAgentDispatch, RoomConfiguration } from "livekit-server-sdk";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  // Anonymous calls are opt-in and carry no account or operator identity.
  if (process.env.CSR_ENABLE_GUEST_ACCESS !== "true") {
    return NextResponse.json({ error: "Guest calls are not enabled" }, { status: 403 });
  }
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) {
    return NextResponse.json({ error: "Invalid request origin" }, { status: 403 });
  }
  if (!process.env.LIVEKIT_URL || !process.env.LIVEKIT_API_KEY || !process.env.LIVEKIT_API_SECRET) {
    return NextResponse.json({ error: "Support is not configured" }, { status: 503 });
  }

  // Ignore client-supplied room, identity, and metadata.
  const room = `mts-support-${randomUUID()}`;
  const token = new AccessToken(process.env.LIVEKIT_API_KEY, process.env.LIVEKIT_API_SECRET, {
    identity: `visitor-${randomUUID()}`,
    name: "MTS visitor",
    ttl: "10m",
  });
  token.addGrant({ roomJoin: true, room, canPublish: true, canPublishData: true, canSubscribe: true });
  token.roomConfig = new RoomConfiguration({
    agents: [new RoomAgentDispatch({
      agentName: "mts-agent",
      metadata: JSON.stringify({ guest: true }),
    })],
  });

  return NextResponse.json(
    { server_url: process.env.LIVEKIT_URL, participant_token: await token.toJwt() },
    { status: 201, headers: { "Cache-Control": "no-store" } },
  );
}
