import fs from "node:fs/promises";
import path from "node:path";
import makeWASocket, {
  DisconnectReason,
  WASocket,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
} from "@whiskeysockets/baileys";
import pino from "pino";
import { DeliveryPayload, InboundPayload, OutboundAttachment, OutboundPayload } from "./protocol.js";

type OnInbound = (payload: InboundPayload) => void;
type OnQR = (qr: string) => void;
type OnError = (msg: string) => void;
type OnConnected = () => void;
type OnDisconnected = (reason: string) => void;

const logger = pino({ level: process.env.BRIDGE_LOG_LEVEL ?? "silent" });

function normalizeJid(jid?: string): string {
  if (!jid) return "";
  if (jid.includes("@")) {
    const [userWithDevice, domain] = jid.split("@");
    const user = userWithDevice.split(":")[0];
    return `${user}@${domain}`;
  }
  return jid;
}

function jidUser(jid?: string): string {
  if (!jid) return "";
  const base = jid.includes("@") ? jid.split("@")[0] : jid;
  return base.split(":")[0] ?? "";
}

function extractText(msg: any): string | undefined {
  return (
    msg?.conversation ??
    msg?.extendedTextMessage?.text ??
    msg?.imageMessage?.caption ??
    msg?.documentMessage?.caption
  );
}

function extractMedia(msg: any): InboundPayload["media"] {
  const out: NonNullable<InboundPayload["media"]> = [];
  if (msg?.imageMessage) {
    out.push({
      type: "image",
      mime_type: msg.imageMessage.mimetype,
      caption: msg.imageMessage.caption,
    });
  }
  if (msg?.documentMessage) {
    out.push({
      type: "document",
      mime_type: msg.documentMessage.mimetype,
      file_name: msg.documentMessage.fileName,
      caption: msg.documentMessage.caption,
    });
  }
  return out.length > 0 ? out : undefined;
}

async function attachmentToMessage(att: OutboundAttachment): Promise<Record<string, unknown>> {
  const filePath = path.resolve(att.path);
  const data = await fs.readFile(filePath);
  if (att.type === "document") {
    return {
      document: data,
      fileName: att.file_name ?? path.basename(filePath),
      mimetype: att.mime_type,
      caption: att.caption,
    };
  }
  return {
    image: data,
    caption: att.caption,
  };
}

export class WhatsAppBridge {
  private sock?: WASocket;
  private readonly sessionDir: string;
  private readonly onInbound: OnInbound;
  private readonly onQR: OnQR;
  private readonly onError: OnError;
  private readonly onConnected?: OnConnected;
  private readonly onDisconnected?: OnDisconnected;
  private reconnecting = false;
  private allowReconnect = true;
  /** Track our own outbound message IDs to suppress echo */
  private readonly recentOutboundIds = new Set<string>();
  /** All known user IDs that belong to "me" (phone number + LID) */
  private readonly selfUsers = new Set<string>();
  /** Cache sent messages for retry decryption (getMessage callback) */
  private readonly msgCache = new Map<string, any>();

  constructor(opts: {
    sessionDir: string;
    onInbound: OnInbound;
    onQR: OnQR;
    onError: OnError;
    onConnected?: OnConnected;
    onDisconnected?: OnDisconnected;
  }) {
    this.sessionDir = opts.sessionDir;
    this.onInbound = opts.onInbound;
    this.onQR = opts.onQR;
    this.onError = opts.onError;
    this.onConnected = opts.onConnected;
    this.onDisconnected = opts.onDisconnected;
  }

  async start(): Promise<void> {
    this.allowReconnect = true;
    await this.connect();
  }

  private async connect(): Promise<void> {
    const { state, saveCreds } = await useMultiFileAuthState(this.sessionDir);
    const { version } = await fetchLatestBaileysVersion();

    // Keep it simple — match the reference working implementation
    const sock = makeWASocket({
      version,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      logger,
      printQRInTerminal: false,
      syncFullHistory: false,
      markOnlineOnConnect: false,
      getMessage: async (key) => {
        const cached = this.msgCache.get(key.id ?? "");
        return cached?.message ?? undefined;
      },
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", ({ connection, qr, lastDisconnect }) => {
      if (qr) {
        this.onQR(qr);
      }
      if (connection === "open") {
        console.log("[bridge] ✅ Connected to WhatsApp");
        // Register our phone number as a self-user
        const meUser = jidUser(normalizeJid(sock.user?.id));
        if (meUser) {
          this.selfUsers.add(meUser);
          console.log(`[bridge] registered self-user: ${meUser}`);
        }
        this.onConnected?.();
      }
      if (connection === "close") {
        const statusCode = (lastDisconnect?.error as any)?.output?.statusCode;
        const loggedOut = statusCode === DisconnectReason.loggedOut;
        this.onDisconnected?.(loggedOut ? "logged_out" : "connection_closed");
        if (this.allowReconnect && !loggedOut && !this.reconnecting) {
          this.reconnecting = true;
          console.log("[bridge] Connection closed, reconnecting in 5s...");
          setTimeout(async () => {
            this.reconnecting = false;
            try {
              await this.connect();
            } catch (err) {
              this.onError(`Reconnection failed: ${String(err)}`);
            }
          }, 5000);
        }
      }
    });

    sock.ev.on("messages.upsert", (evt: any) => {
      if (evt.type !== "notify") return;
      for (const m of evt.messages ?? []) {
        // Skip status broadcasts
        if (m.key.remoteJid === "status@broadcast") continue;
        if (!m.key?.remoteJid || !m.key?.id) continue;

        // Skip our own outbound messages (echo suppression)
        if (this.recentOutboundIds.has(m.key.id)) {
          this.recentOutboundIds.delete(m.key.id);
          continue;
        }

        const message = m.message;
        if (!message) continue;

        const text = extractText(message);
        const media = extractMedia(message);
        if (!text && !media) continue;

        // Determine self-chat using the selfUsers set
        let remoteJid = String(m.key.remoteJid);
        const remoteUser = jidUser(normalizeJid(remoteJid));

        // If this is a fromMe message, learn new self-user IDs (like the LID)
        if (m.key.fromMe && remoteUser && !this.selfUsers.has(remoteUser)) {
          this.selfUsers.add(remoteUser);
          console.log(`[bridge] learned self-user from fromMe: ${remoteUser}`);
        }

        const isSelfChat = Boolean(remoteUser && this.selfUsers.has(remoteUser));

        // Use the original remoteJid as chat_id — do NOT rewrite LID to phone number
        // because the phone's encryption expects the LID for message delivery.

        const payload: InboundPayload = {
          id: m.key.id,
          chat_id: remoteJid,
          sender_id: m.key.participant ?? remoteJid,
          is_self_chat: isSelfChat,
          is_from_me: Boolean(m.key.fromMe),
          text,
          media,
          timestamp: new Date((Number(m.messageTimestamp) || Date.now() / 1000) * 1000).toISOString(),
        };

        console.log(
          `[bridge] inbound id=${payload.id} chat=${payload.chat_id} self=${payload.is_self_chat} fromMe=${payload.is_from_me}`
        );
        this.onInbound(payload);
      }
    });

    this.sock = sock;
  }

  async stop(): Promise<void> {
    this.allowReconnect = false;
    const sock = this.sock;
    this.sock = undefined;
    this.reconnecting = false;
    if (!sock) {
      return;
    }
    try {
      (sock as any).end?.(undefined);
    } catch (err) {
      this.onError(`Stop failed: ${String(err)}`);
    }
  }

  async send(payload: OutboundPayload): Promise<DeliveryPayload> {
    if (!this.sock) {
      throw new Error("WhatsApp socket not ready");
    }

    const msg: Record<string, unknown> = {};
    if (payload.text) {
      msg.text = payload.text;
    }

    if (payload.attachments?.length) {
      const firstAttachment = await attachmentToMessage(payload.attachments[0]);
      Object.assign(msg, firstAttachment);
      if (payload.text && !msg.caption) {
        msg.caption = payload.text;
        delete msg.text;
      }
    }

    console.log(
      `[bridge] outbound request id=${payload.id} chat=${payload.chat_id} text_len=${(payload.text ?? "").length}`
    );

    const result = await this.sock.sendMessage(payload.chat_id, msg as any);
    const providerMessageId = result?.key?.id ?? "";

    // Cache message for retry decryption (getMessage callback)
    if (providerMessageId && result) {
      this.msgCache.set(providerMessageId, result);
      // Auto-clean after 10 min
      setTimeout(() => this.msgCache.delete(providerMessageId), 10 * 60 * 1000);
    }

    // Track outbound ID so we don't echo it back
    if (providerMessageId) {
      this.recentOutboundIds.add(providerMessageId);
      // Auto-clean after 5 min
      setTimeout(() => this.recentOutboundIds.delete(providerMessageId), 5 * 60 * 1000);
    }

    console.log(
      `[bridge] delivery receipt outbound_id=${payload.id} provider_id=${providerMessageId} delivered_chat=${payload.chat_id}`
    );

    return {
      outbound_id: payload.id,
      provider_message_id: providerMessageId,
      chat_id: payload.chat_id,
      timestamp: new Date().toISOString(),
    };
  }
}
