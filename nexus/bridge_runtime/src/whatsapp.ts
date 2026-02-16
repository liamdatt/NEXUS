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

function normalizeJid(raw?: unknown): string {
  if (typeof raw !== "string") {
    return "";
  }
  const jid = raw.trim();
  if (!jid) {
    return "";
  }
  if (!jid.includes("@")) {
    return jid.split(":")[0] ?? "";
  }
  const [userWithDevice, domainRaw] = jid.split("@", 2);
  const user = userWithDevice.split(":")[0] ?? "";
  const domain = (domainRaw ?? "").toLowerCase();
  if (!user || !domain) {
    return "";
  }
  return `${user}@${domain}`;
}

function jidUser(raw?: unknown): string {
  const normalized = normalizeJid(raw);
  if (!normalized) {
    return "";
  }
  if (!normalized.includes("@")) {
    return normalized;
  }
  return normalized.split("@", 1)[0] ?? "";
}

function uniqueJids(...values: unknown[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const normalized = normalizeJid(value);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
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
  /** All known user IDs that belong to "me" (phone number + LID aliases). */
  private readonly selfUsers = new Set<string>();
  /** Known full JID aliases for "me". */
  private readonly selfJids = new Set<string>();
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
      console.log("[bridge] pairing watchdog timeout; recycling socket while remaining in pending_pairing");
      this.closeSocketForRecovery(sock);
    }, pairingQrTimeoutMs);
  }

  private registerSelfIdentity(raw: unknown, source: string): void {
    const normalized = normalizeJid(raw);
    if (!normalized) {
      return;
    }

    let changed = false;
    if (normalized.includes("@") && !this.selfJids.has(normalized)) {
      this.selfJids.add(normalized);
      changed = true;
    }

    const user = jidUser(normalized);
    if (user && !this.selfUsers.has(user)) {
      this.selfUsers.add(user);
      changed = true;
    }

    if (changed) {
      console.log(`[bridge] registered self identity source=${source} jid=${normalized} user=${user || "-"}`);
    }
  }

  private seedSelfIdentities(sock: WASocket, authState: any): void {
    const user = (sock.user ?? {}) as Record<string, unknown>;
    this.registerSelfIdentity(user.id, "sock.user.id");
    this.registerSelfIdentity(user.jid, "sock.user.jid");
    this.registerSelfIdentity(user.lid, "sock.user.lid");

    const credsMe = authState?.creds?.me;
    if (credsMe && typeof credsMe === "object") {
      this.registerSelfIdentity((credsMe as Record<string, unknown>).id, "creds.me.id");
      this.registerSelfIdentity((credsMe as Record<string, unknown>).jid, "creds.me.jid");
      this.registerSelfIdentity((credsMe as Record<string, unknown>).lid, "creds.me.lid");
    } else {
      this.registerSelfIdentity(credsMe, "creds.me");
    }
  }

  private isKnownSelfJid(raw: unknown): boolean {
    const normalized = normalizeJid(raw);
    if (!normalized) {
      return false;
    }
    if (this.selfJids.has(normalized)) {
      return true;
    }
    const user = jidUser(normalized);
    return Boolean(user && this.selfUsers.has(user));
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

    this.seedSelfIdentities(sock, state);

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
        this.seedSelfIdentities(sock, state);
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

        const key = m.key as Record<string, unknown>;
        const remoteJid = String(key.remoteJid);
        const remoteJidAlt = typeof key.remoteJidAlt === "string" ? key.remoteJidAlt : undefined;

        const chatJids = uniqueJids(remoteJid, remoteJidAlt);
        const chatUsers = Array.from(new Set(chatJids.map((jid) => jidUser(jid)).filter(Boolean)));
        const matchedChatJids = chatJids.filter((jid) => this.isKnownSelfJid(jid));
        const matchedChatUsers = chatUsers.filter((user) => this.selfUsers.has(user));
        const isSelfChat = matchedChatJids.length > 0 || matchedChatUsers.length > 0;

        const participant = typeof key.participant === "string" ? key.participant : undefined;
        const participantAlt = typeof key.participantAlt === "string" ? key.participantAlt : undefined;
        const senderJids = uniqueJids(participant, participantAlt);
        const senderUsers = Array.from(new Set(senderJids.map((jid) => jidUser(jid)).filter(Boolean)));

        const isFromMeRaw = Boolean(key.fromMe);
        if (isFromMeRaw) {
          for (const senderJid of senderJids) {
            this.registerSelfIdentity(senderJid, "sender.from_me");
          }
        }

        const senderMatchesSelf =
          senderJids.some((jid) => this.isKnownSelfJid(jid)) ||
          senderUsers.some((user) => this.selfUsers.has(user));
        const hasSenderIdentity = senderJids.length > 0 || senderUsers.length > 0;

        const isFromMe = isFromMeRaw || (isSelfChat && (senderMatchesSelf || !hasSenderIdentity));

        if (!isSelfChat || !isFromMe) {
          const reason = !isSelfChat ? "not_self_chat" : "not_from_me";
          console.log(
            `[bridge] ignored inbound id=${m.key.id} reason=${reason} chat=${remoteJid} chat_alt=${remoteJidAlt ?? "-"} participant=${participant ?? "-"} participant_alt=${participantAlt ?? "-"} from_me_raw=${isFromMeRaw}`
          );
          continue;
        }

        const payload: InboundPayload = {
          id: m.key.id,
          chat_id: remoteJid,
          sender_id: participantAlt ?? participant ?? remoteJidAlt ?? remoteJid,
          is_self_chat: isSelfChat,
          is_from_me: isFromMe,
          text,
          media,
          timestamp: new Date((Number(m.messageTimestamp) || Date.now() / 1000) * 1000).toISOString(),
        };

        console.log(
          `[bridge] inbound id=${payload.id} chat=${payload.chat_id} sender=${payload.sender_id} self=${payload.is_self_chat} fromMe=${payload.is_from_me}`
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
