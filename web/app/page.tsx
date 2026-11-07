"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlarmClock,
  ArrowLeft,
  AudioLines,
  ExternalLink,
  MessageCircle,
  Mic,
  MicOff,
  Phone,
  PhoneOff,
  Send,
  X,
} from "lucide-react";
import {
  RoomAudioRenderer,
  SessionProvider,
  useAgent,
  useSession,
  useSessionMessages,
} from "@livekit/components-react";
import { TokenSource } from "livekit-client";

const MTS_SITE = "https://mts.192-888-702-801.site";
const BROWSER_PATHS = new Set([
  "/",
  "/portal",
  "/portal/shop",
  "/portal/tickets",
  "/portal/orders",
  "/portal/billing",
]);

function playRingingAudio(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const audio = new Audio("/8sec-ringing.mp3");
    audio.volume = 0.7;

    const cleanup = () => {
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("error", onError);
      signal.removeEventListener("abort", onAbort);
    };
    const onEnded = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      audio.pause();
      reject(new Error("The rings could not play. Check your browser sound and try again."));
    };
    const onAbort = () => {
      cleanup();
      audio.pause();
      reject(new DOMException("Call cancelled", "AbortError"));
    };

    if (signal.aborted) {
      onAbort();
      return;
    }
    audio.addEventListener("ended", onEnded, { once: true });
    audio.addEventListener("error", onError, { once: true });
    signal.addEventListener("abort", onAbort, { once: true });
    void audio.play().catch(onError);
  });
}

export default function Page() {
  const [sessionNumber, setSessionNumber] = useState(0);
  return <SupportCall key={sessionNumber} onRestart={() => setSessionNumber((n) => n + 1)} />;
}

function SupportCall({ onRestart }: { onRestart: () => void }) {
  const handoff = useRef("");
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.data?.type !== "mts-user" || typeof event.data.code !== "string") return;
      handoff.current = event.data.code.slice(0, 80);
    }
    window.addEventListener("message", onMessage);
    window.parent?.postMessage({ type: "mts-agent-ready" }, "*");
    return () => window.removeEventListener("message", onMessage);
  }, []);
  const tokenSource = useMemo(
    () =>
      TokenSource.custom(async () => {
        const started = Date.now();
        while (!handoff.current && Date.now() - started < 2500) {
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        const response = await fetch("/api/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: handoff.current }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : "The call could not connect.");
        return {
          serverUrl: data.serverUrl || data.server_url,
          participantToken: data.participantToken || data.participant_token,
        };
      }),
    [],
  );
  const session = useSession(tokenSource);

  return (
    <SessionProvider session={session}>
      <CallSurface session={session} onRestart={onRestart} />
      <RoomAudioRenderer />
    </SessionProvider>
  );
}

type Session = ReturnType<typeof useSession>;

function CallSurface({ session, onRestart }: { session: Session; onRestart: () => void }) {
  const agent = useAgent(session);
  const { messages, send, isSending } = useSessionMessages(session);
  const [phase, setPhase] = useState<"incoming" | "ringing" | "connecting" | "active" | "ended">("incoming");
  const [chatOpen, setChatOpen] = useState(false);
  const [micOn, setMicOn] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [reminder, setReminder] = useState("");
  const [browserPath, setBrowserPath] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [duration, setDuration] = useState(0);
  const starting = useRef(false);
  const callAttempt = useRef<AbortController | null>(null);
  const chatScroll = useRef<HTMLDivElement>(null);
  const reminderTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (startedAt === null) return;
    const timer = setInterval(() => setDuration(Math.floor((Date.now() - startedAt) / 1000)), 1_000);
    return () => clearInterval(timer);
  }, [startedAt]);

  useEffect(() => {
    if (chatScroll.current) chatScroll.current.scrollTop = chatScroll.current.scrollHeight;
  }, [messages.length, chatOpen]);

  useEffect(() => {
    const room = session.room;
    room.registerRpcMethod("mts.openWebsite", async (data) => {
      const path = JSON.parse(data.payload)?.path;
      if (typeof path !== "string" || !BROWSER_PATHS.has(path)) {
        throw new Error("Unsupported MTS page");
      }
      setBrowserPath(path);
      return JSON.stringify({ ready: true, path });
    });
    return () => {
      callAttempt.current?.abort();
      room.unregisterRpcMethod("mts.openWebsite");
      if (reminderTimer.current) clearTimeout(reminderTimer.current);
      void session.end();
    };
    // The Room instance is stable for this call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.room]);

  useEffect(() => {
    const resume = () => {
      void session.room.startAudio().catch(() => undefined);
    };
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: "Sarah",
        artist: "Manage Tech Service",
      });
      navigator.mediaSession.playbackState = "playing";
    }
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("focus", resume);
    window.addEventListener("pageshow", resume);
    const keep = window.setInterval(resume, 15000);
    return () => {
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("focus", resume);
      window.removeEventListener("pageshow", resume);
      window.clearInterval(keep);
    };
  }, [session.room]);

  async function start(kind: "voice" | "chat") {
    if (starting.current || phase !== "incoming") return;
    starting.current = true;
    const attempt = new AbortController();
    callAttempt.current = attempt;
    setPhase(kind === "voice" ? "ringing" : "connecting");
    setError("");
    setChatOpen(kind === "chat");
    try {
      if (kind === "voice") {
        await playRingingAudio(attempt.signal);
        if (attempt.signal.aborted) return;
        setPhase("connecting");
      }
      await session.start({ tracks: { microphone: { enabled: false } } });
      if (attempt.signal.aborted) {
        await session.end();
        return;
      }
      setStartedAt(Date.now());
      setPhase("active");
      if (kind === "voice") {
        try {
          await session.room.localParticipant.setMicrophoneEnabled(true);
          setMicOn(true);
        } catch {
          setError("Microphone access was denied. You can still message Sarah.");
          setChatOpen(true);
        }
      }
    } catch (cause) {
      if (!attempt.signal.aborted) {
        setPhase("incoming");
        setError(cause instanceof Error ? cause.message : "The call could not connect.");
      }
    } finally {
      if (callAttempt.current === attempt) callAttempt.current = null;
      starting.current = false;
    }
  }

  async function toggleMic() {
    try {
      await session.room.localParticipant.setMicrophoneEnabled(!micOn);
      setMicOn(!micOn);
      setError("");
    } catch {
      setError("Microphone access was denied. Check your browser permissions.");
    }
  }

  async function endCall() {
    callAttempt.current?.abort();
    if (session.isConnected) await session.end();
    setPhase("ended");
    setMicOn(false);
    setChatOpen(false);
    setStartedAt(null);
  }

  function remind() {
    if (reminderTimer.current) clearTimeout(reminderTimer.current);
    setReminder("Reminder set for 10 minutes while this page stays open.");
    reminderTimer.current = setTimeout(() => {
      setReminder("A reminder: Sarah is ready when you are.");
    }, 10 * 60 * 1_000);
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || !session.isConnected || isSending) return;
    try {
      await send(message);
      setDraft("");
      setError("");
    } catch {
      setError("Message was not sent. Please try again.");
    }
  }

  const callTime = `${String(Math.floor(duration / 60)).padStart(2, "0")}:${String(duration % 60).padStart(2, "0")}`;
  const canChat = session.isConnected && agent.state !== "failed";
  const callLabel = phase === "incoming" ? "Incoming call" : phase === "ringing" ? "Ringing…" : phase === "connecting" ? "Connecting call" : phase === "ended" ? "Call ended" : agent.state === "speaking" ? "Sarah is speaking" : agent.state === "thinking" ? "Sarah is thinking" : "Connected";

  return (
    <main className="call-page">
      <div className="phone" aria-label="MTS support call">
        <div className="phone-screen">
          <div className="status-bar" aria-hidden="true">
            <span className="dynamic-island" />
          </div>

          {chatOpen ? (
            <section className="chat-view" aria-label="Chat with Sarah">
              <header className="chat-header">
                <button className="header-button" onClick={() => setChatOpen(false)} aria-label="Back to call"><ArrowLeft size={23} /></button>
                <div className="chat-contact"><span className="chat-avatar"><AudioLines size={20} /></span><span><strong>Sarah</strong><small>MTS Customer Support</small></span></div>
                <button className="header-button" onClick={endCall} aria-label="End conversation"><X size={21} /></button>
              </header>
              <div className="chat-messages" ref={chatScroll}>
                {messages.length === 0 && <div className="chat-empty"><span><MessageCircle size={24} /></span><h2>Message Sarah</h2><p>Ask about MTS services or get general support. Sign in through the MTS portal for account details.</p></div>}
                {messages.map((message) => {
                  const own = message.type === "userTranscript" || message.from?.identity === session.room.localParticipant.identity;
                  return <div className={`chat-message ${own ? "mine" : "theirs"}`} key={message.id}><span className="chat-message-label">{own ? "You" : "Sarah"}</span><div>{message.message}</div></div>;
                })}
              </div>
              {browserPath && <a className="prepared-link" href={`${MTS_SITE}${browserPath}`} target="_blank" rel="noopener noreferrer">Open MTS page <ExternalLink size={15} /></a>}
              {error && <p className="chat-error" role="alert">{error}</p>}
              <form className="chat-composer" onSubmit={sendMessage}>
                <label className="sr-only" htmlFor="chat-input">Message Sarah</label>
                <input id="chat-input" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={canChat ? "Message Sarah…" : "Connecting…"} disabled={!canChat} />
                <button type="submit" disabled={!canChat || !draft.trim() || isSending} aria-label="Send message"><Send size={18} /></button>
              </form>
            </section>
          ) : (
            <section className="call-view" aria-label="Call with Sarah">
              <div className="caller-heading"><p>{callLabel}</p><h1>Sarah</h1><span>Manage Tech Services</span>{phase === "active" && <small>{callTime}</small>}</div>
              <div className="call-spacer" />
              {error && <p className="call-error" role="alert">{error}</p>}
              {reminder && <p className="reminder-note" role="status">{reminder}</p>}
              {phase === "incoming" || phase === "ringing" || phase === "connecting" ? (
                <div className="call-actions">
                  <div className="secondary-actions"><button onClick={() => void start("chat")} disabled={phase !== "incoming"}><MessageCircle size={24} fill="currentColor" /><span>Message</span></button><button onClick={remind}><AlarmClock size={24} /><span>Remind</span></button></div>
                  <div className="primary-actions"><button className="answer-button" onClick={() => void start("voice")} disabled={phase !== "incoming"} aria-label="Answer call"><Phone size={33} fill="currentColor" /></button><button className="decline-button" onClick={() => void endCall()} aria-label="Decline call"><PhoneOff size={32} fill="currentColor" /></button></div>
                </div>
              ) : phase === "active" ? (
                <div className="call-actions"><div className="secondary-actions"><button onClick={() => setChatOpen(true)}><MessageCircle size={24} fill="currentColor" /><span>Message</span></button><button onClick={() => void toggleMic()}>{micOn ? <Mic size={24} /> : <MicOff size={24} />}<span>{micOn ? "Mute" : "Unmute"}</span></button></div><div className="primary-actions active-actions"><button className="decline-button" onClick={() => void endCall()} aria-label="End call"><PhoneOff size={32} fill="currentColor" /></button></div></div>
              ) : (
                <div className="ended-actions"><button onClick={onRestart}><Phone size={20} /> Call Sarah again</button></div>
              )}
            </section>
          )}
          <div className="home-indicator" aria-hidden="true" />
        </div>
      </div>
    </main>
  );
}
