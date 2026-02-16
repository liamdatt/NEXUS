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
import {
  ConnectionUpdatePayload,
  DeliveryPayload,
  InboundPayload,
  OutboundAttachment,
  OutboundPayload,
} from "./protocol.js";

type OnInbound = (payload: InboundPayload) => void;
type OnQR = (qr: string) => void;
type OnError = (msg: string) => void;
type OnConnected = () => void;
type OnDisconnected = (reason: string) => void;
type OnConnectionUpdate = (payload: ConnectionUpdatePayload) => void;

const logger = pino({ level: process.env.BRIDGE_LOG_LEVEL ?? "silent" });
const qrTimeoutRaw = Number(process.env.BRIDGE_PAIRING_QR_TIMEOUT_MS ?? "30000");
const pairingQrTimeoutMs =
  Number.isFinite(qrTimeoutRaw) && qrTimeoutRaw > 0 ? Math.floor(qrTimeoutRaw) : 30000;

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
  private readonly onConnectionUpdate?: OnConnectionUpdate;
  private reconnecting = false;
  private allowReconnect = true;
  private qrWatchdog?: ReturnType<typeof setTimeout>;
  private connectAttempt = 0;
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
    onConnectionUpdate?: OnConnectionUpdate;
  }) {
    this.sessionDir = opts.sessionDir;
    this.onInbound = opts.onInbound;
    this.onQR = opts.onQR;
    this.onError = opts.onError;
    this.onConnected = opts.onConnected;
    this.onDisconnected = opts.onDisconnected;
    this.onConnectionUpdate = opts.onConnectionUpdate;
  }

  async start(): Promise<void> {
    this.allowReconnect = true;
    await this.connect();
  }

  private clearQrWatchdog(): void {
    if (!this.qrWatchdog) {
      return;
    }
    clearTimeout(this.qrWatchdog);
    this.qrWatchdog = undefined;
  }

  private closeSocketForRecovery(sock: WASocket): void {
    try {
      (sock as any).end?.(undefined);
    } catch (err) {
      this.onError(`pairing_timeout_close_failed: ${String(err)}`);
    }
  }

  private startQrWatchdog(sock: WASocket, attempt: number): void {
    this.clearQrWatchdog();
    this.qrWatchdog = setTimeout(() => {
      if (!this.allowReconnect) {
        return;
      }
      if (this.sock !== sock || this.connectAttempt !== attempt) {
        return;
      }
      this.onError("pairing_timeout_waiting_for_qr");
      this.closeSocketForRecovery(sock);
    }, pairingQrTimeoutMs);
  }

  private async connect(): Promise<void> {
    const attempt = ++this.connectAttempt;
    const { state, saveCreds } = await useMultiFileAuthState(this.sessionDir);
    const { version } = await fetchLatestBaileysVersion();

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
      const statusCode = (lastDisconnect?.error as any)?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      const reconnectScheduled =
        connection === "close" && this.allowReconnect && !loggedOut && !this.reconnecting;

      this.onConnectionUpdate?.({
        connection: connection ?? "unknown",
        has_qr: Boolean(qr),
        status_code: typeof statusCode === "number" ? statusCode : undefined,
        logged_out: loggedOut,
        reconnect_scheduled: reconnectScheduled,
        timestamp: new Date().toISOString(),
      });

      if (qr) {
        this.clearQrWatchdog();
        this.onQR(qr);
      }

      if (connection === "open") {
        this.clearQrWatchdog();
        console.log("[bridge] ✅ Connected to WhatsApp");
        const meUser = jidUser(normalizeJid(sock.user?.id));
        if (meUser) {
          this.selfUsers.add(meUser);
          console.log(`[bridge] registered self-user: ${meUser}`);
        }
        this.onConnected?.();
      }

      if (connection === "close") {
        this.clearQrWatchdog();
        this.onDisconnected?.(loggedOut ? "logged_out" : "connection_closed");
        if (reconnectScheduled) {
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
        if (m.key.remoteJid === "status@broadcast") continue;
        if (!m.key?.remoteJid || !m.key?.id) continue;

        if (this.recentOutboundIds.has(m.key.id)) {
          this.recentOutboundIds.delete(m.key.id);
          continue;
        }

        const message = m.message;
        if (!message) continue;

        const text = extractText(message);
        const media = extractMedia(message);
        if (!text && !media) continue;

        const remoteJid = String(m.key.remoteJid);
        const remoteUser = jidUser(normalizeJid(remoteJid));

        const isFromMe = Boolean(m.key.fromMe);
        const isSelfChat = Boolean(remoteUser && this.selfUsers.has(remoteUser));
        if (!isSelfChat || !isFromMe) {
          const reason = !isSelfChat ? "not_self_chat" : "not_from_me";
          console.log(`[bridge] ignored inbound id=${m.key.id} chat=${remoteJid} reason=${reason}`);
          continue;
        }

        const payload: InboundPayload = {
          id: m.key.id,
          chat_id: remoteJid,
          sender_id: m.key.participant ?? remoteJid,
          is_self_chat: isSelfChat,
          is_from_me: isFromMe,
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
    this.startQrWatchdog(sock, attempt);
  }

  async stop(): Promise<void> {
    this.allowReconnect = false;
    this.clearQrWatchdog();
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

    if (providerMessageId && result) {
      this.msgCache.set(providerMessageId, result);
      setTimeout(() => this.msgCache.delete(providerMessageId), 10 * 60 * 1000);
    }

    if (providerMessageId) {
      this.recentOutboundIds.add(providerMessageId);
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
