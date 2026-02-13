import "dotenv/config";
import { randomUUID } from "node:crypto";
import { WebSocketServer, WebSocket } from "ws";
import qrcode from "qrcode-terminal";
import { ConnectionUpdatePayload, Envelope, InboundPayload, OutboundPayload } from "./protocol.js";
import { WhatsAppBridge } from "./whatsapp.js";

const host = process.env.BRIDGE_HOST ?? "0.0.0.0";
const port = Number(process.env.BRIDGE_PORT ?? 8765);
const sessionDir = process.env.BRIDGE_SESSION_DIR ?? "./session";
const sharedSecret = process.env.BRIDGE_SHARED_SECRET;
const qrMode = (process.env.BRIDGE_QR_MODE ?? "url").toLowerCase();
const exitOnConnect = process.env.BRIDGE_EXIT_ON_CONNECT === "1";
const exitDelayRaw = Number(process.env.BRIDGE_EXIT_ON_CONNECT_DELAY_MS ?? "20000");
const exitOnConnectDelayMs =
  Number.isFinite(exitDelayRaw) && exitDelayRaw >= 0 ? Math.floor(exitDelayRaw) : 20000;

const clients = new Set<WebSocket>();
let exitScheduled = false;

function makeEnvelope<T>(event: Envelope["event"], payload: T): Envelope<T> {
  return {
    event,
    payload,
    message_id: randomUUID(),
    trace_id: randomUUID(),
    channel: "whatsapp",
    timestamp: new Date().toISOString(),
  };
}

function broadcast<T>(event: Envelope["event"], payload: T): void {
  const msg = JSON.stringify(makeEnvelope(event, payload));
  for (const ws of clients) {
    if (ws.readyState === ws.OPEN) {
      ws.send(msg);
    }
  }
}

function parseClientName(header: string | string[] | undefined): string {
  if (Array.isArray(header)) {
    return (header[0] ?? "").trim() || "unknown";
  }
  return (header ?? "").trim() || "unknown";
}

const wss = new WebSocketServer({
  host,
  port,
  verifyClient: ({ req }: any) => {
    if (!sharedSecret) return true;
    const got = req.headers["x-nexus-secret"];
    if (Array.isArray(got)) {
      return got.includes(sharedSecret);
    }
    return got === sharedSecret;
  },
});

let shuttingDown = false;

async function shutdown(exitCode: number): Promise<void> {
  if (shuttingDown) {
    return;
  }
  shuttingDown = true;

  for (const ws of clients) {
    try {
      ws.close();
    } catch {
      // Ignore close errors during shutdown.
    }
  }
  clients.clear();

  await bridge.stop();
  await new Promise<void>((resolve) => {
    wss.close(() => resolve());
  });
  process.exit(exitCode);
}

const bridge = new WhatsAppBridge({
  sessionDir,
  onInbound: (payload: InboundPayload) => {
    console.log(
      `[bridge] inbound id=${payload.id} chat=${payload.chat_id} sender=${payload.sender_id} self=${payload.is_self_chat} fromMe=${payload.is_from_me}`
    );
    broadcast("bridge.inbound_message", payload);
  },
  onQR: (qr: string) => {
    if (qrMode === "terminal") {
      console.log("WhatsApp pairing QR (scan via Linked Devices):");
      qrcode.generate(qr, { small: true });
    } else {
      const qrUrl = `https://quickchart.io/qr?text=${encodeURIComponent(qr)}&size=320`;
      console.log("WhatsApp pairing QR URL (open in browser, then scan via Linked Devices):");
      console.log(qrUrl);
    }
    broadcast("bridge.qr", { qr });
  },
  onError: (error: string) => {
    broadcast("bridge.error", { error });
  },
  onConnectionUpdate: (payload: ConnectionUpdatePayload) => {
    broadcast("bridge.connection_update", payload);
  },
  onConnected: () => {
    broadcast("bridge.connected", { status: "connected" });
    if (exitOnConnect && !exitScheduled) {
      exitScheduled = true;
      console.log(
        `[bridge] BRIDGE_EXIT_ON_CONNECT=1; waiting ${exitOnConnectDelayMs}ms before shutdown to finish pairing.`
      );
      setTimeout(() => {
        void shutdown(0);
      }, exitOnConnectDelayMs);
    }
  },
  onDisconnected: (reason: string) => {
    broadcast("bridge.disconnected", { reason });
  },
});

wss.on("connection", (ws, req) => {
  const clientName = parseClientName(req.headers["x-nexus-client"]);
  clients.add(ws);
  console.log(`[bridge] core ws connected client=${clientName} (clients=${clients.size})`);
  ws.send(JSON.stringify(makeEnvelope("bridge.ready", { status: "ok" })));

  ws.on("message", async (raw) => {
    try {
      const env = JSON.parse(String(raw)) as Envelope<OutboundPayload>;
      if (env.event === "core.outbound_message") {
        console.log(
          `[bridge] outbound request id=${env.payload.id} chat=${env.payload.chat_id} text_len=${(env.payload.text ?? "").length}`
        );
        const receipt = await bridge.send(env.payload);
        console.log(
          `[bridge] delivery receipt outbound_id=${receipt.outbound_id} provider_id=${receipt.provider_message_id} delivered_chat=${receipt.chat_id}`
        );
        broadcast("bridge.delivery_receipt", receipt);
      }
    } catch (err) {
      console.error("[bridge] outbound send failed", err);
      broadcast("bridge.error", { error: String(err) });
    }
  });

  ws.on("close", () => {
    clients.delete(ws);
    console.log(`[bridge] core ws disconnected client=${clientName} (clients=${clients.size})`);
  });
});

bridge.start().catch((err) => {
  console.error("[bridge] startup failed", err);
  broadcast("bridge.error", { error: `startup_failed: ${String(err)}` });
  void shutdown(1);
});

process.on("SIGINT", () => {
  void shutdown(0);
});

process.on("SIGTERM", () => {
  void shutdown(0);
});

console.log(`nexus-bridge listening ws://${host}:${port}`);
