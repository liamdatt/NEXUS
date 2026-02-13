export type BridgeEvent =
  | "bridge.ready"
  | "bridge.qr"
  | "bridge.connected"
  | "bridge.disconnected"
  | "bridge.connection_update"
  | "bridge.inbound_message"
  | "bridge.delivery_receipt"
  | "bridge.error"
  | "core.outbound_message"
  | "core.ack";

export interface Envelope<T = unknown> {
  event: BridgeEvent;
  message_id: string;
  timestamp: string;
  channel: "whatsapp";
  trace_id: string;
  payload: T;
}

export interface InboundMedia {
  type: "image" | "document";
  mime_type?: string;
  file_name?: string;
  caption?: string;
}

export interface InboundPayload {
  id: string;
  chat_id: string;
  sender_id: string;
  is_self_chat: boolean;
  is_from_me: boolean;
  text?: string;
  media?: InboundMedia[];
  timestamp: string;
}

export interface OutboundAttachment {
  type: "document" | "image";
  path: string;
  file_name?: string;
  mime_type?: string;
  caption?: string;
}

export interface OutboundPayload {
  id: string;
  chat_id: string;
  text?: string;
  attachments?: OutboundAttachment[];
  reply_to?: string;
}

export interface DeliveryPayload {
  outbound_id: string;
  provider_message_id: string;
  chat_id: string;
  timestamp: string;
}

export interface ConnectionUpdatePayload {
  connection: string;
  has_qr: boolean;
  status_code?: number;
  logged_out: boolean;
  reconnect_scheduled: boolean;
  timestamp: string;
}
