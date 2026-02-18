import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import makeWASocket, {
  DisconnectReason,
  WASocket,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  downloadMediaMessage,
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

const mediaDir = path.resolve(process.env.BRIDGE_MEDIA_DIR ?? "/data/state/workspace/inbox");
const mediaMaxBytesRaw = Number(process.env.BRIDGE_MEDIA_MAX_BYTES ?? `${50 * 1024 * 1024}`);
const mediaMaxBytes =
  Number.isFinite(mediaMaxBytesRaw) && mediaMaxBytesRaw > 0
    ? Math.floor(mediaMaxBytesRaw)
    : 50 * 1024 * 1024;
const mediaRetentionHoursRaw = Number(process.env.BRIDGE_MEDIA_RETENTION_HOURS ?? "168");
const mediaRetentionMs =
  Number.isFinite(mediaRetentionHoursRaw) && mediaRetentionHoursRaw > 0
    ? Math.floor(mediaRetentionHoursRaw * 60 * 60 * 1000)
    : 168 * 60 * 60 * 1000;
const mediaCleanupIntervalMs = 60 * 60 * 1000;

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

function sanitizePathSegment(raw: string): string {
  const cleaned = raw.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/_+/g, "_").slice(0, 80);
  return cleaned || "file";
}

function extensionFromMime(mimeType: string, mediaType: "image" | "document"): string {
  const lowered = mimeType.toLowerCase();
  if (lowered.includes("png")) return ".png";
  if (lowered.includes("jpeg") || lowered.includes("jpg")) return ".jpg";
  if (lowered.includes("webp")) return ".webp";
  if (lowered.includes("gif")) return ".gif";
  if (lowered.includes("pdf")) return ".pdf";
  if (lowered.includes("csv")) return ".csv";
  if (lowered.includes("sheet") || lowered.includes("excel") || lowered.includes("spreadsheetml")) return ".xlsx";
  if (lowered.includes("word")) return ".docx";
  if (lowered.includes("json")) return ".json";
  if (lowered.includes("plain")) return ".txt";
  return mediaType === "image" ? ".bin" : ".dat";
}

function toSizeBytes(raw: unknown): number | undefined {
  if (typeof raw === "number" && Number.isFinite(raw) && raw >= 0) {
    return Math.floor(raw);
  }
  if (typeof raw === "bigint" && raw >= 0n) {
    return Number(raw);
  }
  if (typeof raw === "string") {
    const parsed = Number(raw);
    if (Number.isFinite(parsed) && parsed >= 0) {
      return Math.floor(parsed);
    }
    return undefined;
  }
  if (raw && typeof raw === "object") {
    const asObj = raw as { toString?: () => string };
    if (typeof asObj.toString === "function") {
      const parsed = Number(asObj.toString());
      if (Number.isFinite(parsed) && parsed >= 0) {
        return Math.floor(parsed);
      }
    }
  }
  return undefined;
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
  /** Best-effort media retention cleanup cadence. */
  private lastMediaCleanupAtMs = 0;

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

  private async maybeCleanupMediaDir(): Promise<void> {
    const now = Date.now();
    if (now - this.lastMediaCleanupAtMs < mediaCleanupIntervalMs) {
      return;
    }
    this.lastMediaCleanupAtMs = now;

    const deadlineMs = now - mediaRetentionMs;
    const walk = async (dirPath: string): Promise<void> => {
      let entries;
      try {
        entries = await fs.readdir(dirPath, { withFileTypes: true });
      } catch {
        return;
      }

      for (const entry of entries) {
        const absolute = path.join(dirPath, String(entry.name));
        if (entry.isDirectory()) {
          await walk(absolute);
          try {
            await fs.rmdir(absolute);
          } catch {
            // non-empty directories are expected
          }
          continue;
        }
        if (!entry.isFile()) {
          continue;
        }
        try {
          const stat = await fs.stat(absolute);
          if (stat.mtimeMs < deadlineMs) {
            await fs.unlink(absolute);
          }
        } catch {
          // best effort cleanup only
        }
      }
    };

    await walk(mediaDir);
  }

  private async saveInboundMedia(
    sock: WASocket,
    messageEnvelope: any,
    mediaType: "image" | "document",
    mediaNode: any,
  ): Promise<NonNullable<InboundPayload["media"]>[number]> {
    const mimeType = typeof mediaNode?.mimetype === "string" ? mediaNode.mimetype : undefined;
    const fileName =
      typeof mediaNode?.fileName === "string" && mediaNode.fileName.trim()
        ? mediaNode.fileName.trim()
        : undefined;
    const caption = typeof mediaNode?.caption === "string" ? mediaNode.caption : undefined;

    const sizeBytes = toSizeBytes(mediaNode?.fileLength);
    if (typeof sizeBytes === "number" && sizeBytes > mediaMaxBytes) {
      return {
        type: mediaType,
        mime_type: mimeType,
        file_name: fileName,
        caption,
        size_bytes: sizeBytes,
        download_status: "skipped",
        download_error: `file_too_large>${mediaMaxBytes}`,
      };
    }

    try {
      await fs.mkdir(mediaDir, { recursive: true });
      await this.maybeCleanupMediaDir();

      const payload = (await downloadMediaMessage(
        messageEnvelope,
        "buffer",
        {},
        {
          logger,
          reuploadRequest: sock.updateMediaMessage,
        }
      )) as Buffer;

      if (!Buffer.isBuffer(payload)) {
        throw new Error("download did not return a binary buffer");
      }
      if (payload.length > mediaMaxBytes) {
        return {
          type: mediaType,
          mime_type: mimeType,
          file_name: fileName,
          caption,
          size_bytes: payload.length,
          download_status: "skipped",
          download_error: `file_too_large>${mediaMaxBytes}`,
        };
      }

      const remote = sanitizePathSegment(String(messageEnvelope?.key?.remoteJid ?? "unknown_chat"));
      const dateKey = new Date().toISOString().slice(0, 10);
      const targetDir = path.join(mediaDir, "whatsapp", remote, dateKey);
      await fs.mkdir(targetDir, { recursive: true });

      const extFromName = path.extname(fileName ?? "").toLowerCase();
      const ext = extFromName || extensionFromMime(mimeType ?? "", mediaType);
      const base = sanitizePathSegment(path.basename(fileName ?? `${mediaType}`, ext));
      const messageId = sanitizePathSegment(String(messageEnvelope?.key?.id ?? "message"));
      const stamped = new Date().toISOString().replace(/[:.]/g, "-");
      const targetPath = path.join(targetDir, `${stamped}-${messageId}-${base}${ext}`);

      await fs.writeFile(targetPath, payload);
      const checksum = createHash("sha256").update(payload).digest("hex");

      return {
        type: mediaType,
        mime_type: mimeType,
        file_name: fileName ?? path.basename(targetPath),
        caption,
        local_path: targetPath,
        size_bytes: payload.length,
        sha256: checksum,
        download_status: "downloaded",
      };
    } catch (err) {
      return {
        type: mediaType,
        mime_type: mimeType,
        file_name: fileName,
        caption,
        size_bytes: sizeBytes,
        download_status: "failed",
        download_error: String(err),
      };
    }
  }

  private async extractMedia(sock: WASocket, messageEnvelope: any, msg: any): Promise<InboundPayload["media"]> {
    const out: NonNullable<InboundPayload["media"]> = [];
    if (msg?.imageMessage) {
      out.push(await this.saveInboundMedia(sock, messageEnvelope, "image", msg.imageMessage));
    }
    if (msg?.documentMessage) {
      out.push(await this.saveInboundMedia(sock, messageEnvelope, "document", msg.documentMessage));
    }
    return out.length > 0 ? out : undefined;
  }

  private trackOutboundProviderMessageId(providerMessageId: string, result: any): void {
    if (!providerMessageId) {
      return;
    }
    if (result) {
      this.msgCache.set(providerMessageId, result);
      setTimeout(() => this.msgCache.delete(providerMessageId), 10 * 60 * 1000);
    }
    this.recentOutboundIds.add(providerMessageId);
    setTimeout(() => this.recentOutboundIds.delete(providerMessageId), 5 * 60 * 1000);
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

    sock.ev.on("messages.upsert", async (evt: any) => {
      if (evt.type !== "notify") return;
      for (const m of evt.messages ?? []) {
        try {
          if (m.key.remoteJid === "status@broadcast") continue;
          if (!m.key?.remoteJid || !m.key?.id) continue;

          if (this.recentOutboundIds.has(m.key.id)) {
            this.recentOutboundIds.delete(m.key.id);
            continue;
          }

          const message = m.message;
          if (!message) continue;

          const text = extractText(message);
          const media = await this.extractMedia(sock, m, message);
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
        } catch (err) {
          this.onError(`inbound_processing_failed: ${String(err)}`);
        }
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

    console.log(
      `[bridge] outbound request id=${payload.id} chat=${payload.chat_id} text_len=${(payload.text ?? "").length} attachments=${payload.attachments?.length ?? 0}`
    );

    const providerMessageIds: string[] = [];

    if (payload.attachments?.length) {
      for (let idx = 0; idx < payload.attachments.length; idx += 1) {
        const att = payload.attachments[idx];
        const msg = await attachmentToMessage(att);
        if (idx === 0 && payload.text && typeof msg.caption !== "string") {
          msg.caption = payload.text;
        }
        const result = await this.sock.sendMessage(payload.chat_id, msg as any);
        const providerMessageId = result?.key?.id ?? "";
        if (providerMessageId) {
          providerMessageIds.push(providerMessageId);
          this.trackOutboundProviderMessageId(providerMessageId, result);
        }
      }
    } else {
      const text = payload.text ?? "";
      const result = await this.sock.sendMessage(payload.chat_id, { text } as any);
      const providerMessageId = result?.key?.id ?? "";
      if (providerMessageId) {
        providerMessageIds.push(providerMessageId);
        this.trackOutboundProviderMessageId(providerMessageId, result);
      }
    }

    const primaryProviderId = providerMessageIds[providerMessageIds.length - 1] ?? "";
    console.log(
      `[bridge] delivery receipt outbound_id=${payload.id} provider_id=${primaryProviderId} delivered_chat=${payload.chat_id} provider_ids=${providerMessageIds.join(",")}`
    );

    return {
      outbound_id: payload.id,
      provider_message_id: primaryProviderId,
      provider_message_ids: providerMessageIds,
      chat_id: payload.chat_id,
      timestamp: new Date().toISOString(),
    };
  }
}
