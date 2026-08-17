const encoder = new TextEncoder();

export type WhatsAppInboundText = {
  sender: string;
  phoneNumberId: string;
  externalMessageId: string;
  text: string;
  timestamp: string | null;
};

export type WhatsAppDeliveryStatus = {
  phoneNumberId: string;
  providerMessageId: string;
  recipientId: string;
  status: string;
  timestamp: string | null;
  errors: Array<{
    code: string;
    title: string;
    message: string;
    details: string;
  }>;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function normalizePhoneNumber(value: string): string | null {
  const normalized = value.replace(/\D/g, "");
  return normalized.length >= 8 && normalized.length <= 20 ? normalized : null;
}

export function whatsappPhoneAliases(value: string): string[] {
  const normalized = normalizePhoneNumber(value);
  if (!normalized) return [];
  const aliases = [normalized];
  if (normalized.startsWith("55")) {
    if (normalized.length === 13 && normalized[4] === "9") {
      aliases.push(normalized.slice(0, 4) + normalized.slice(5));
    } else if (normalized.length === 12) {
      aliases.push(normalized.slice(0, 4) + "9" + normalized.slice(4));
    }
  }
  return aliases;
}

export function phoneNumbersEquivalent(left: string, right: string): boolean {
  const rightAliases = new Set(whatsappPhoneAliases(right));
  return whatsappPhoneAliases(left).some((alias) => rightAliases.has(alias));
}

export function parseWebhookMessages(payload: unknown): WhatsAppInboundText[] {
  const root = asRecord(payload);
  if (!root) return [];

  const result: WhatsAppInboundText[] = [];
  for (const rawEntry of asArray(root.entry)) {
    const entry = asRecord(rawEntry);
    if (!entry) continue;
    for (const rawChange of asArray(entry.changes)) {
      const change = asRecord(rawChange);
      if (!change || change.field !== "messages") continue;
      const value = asRecord(change.value);
      const metadata = value ? asRecord(value.metadata) : null;
      const phoneNumberId = String(metadata?.phone_number_id ?? "");
      if (!value || !phoneNumberId) continue;

      for (const rawMessage of asArray(value.messages)) {
        const message = asRecord(rawMessage);
        if (!message || message.type !== "text") continue;
        const textPayload = asRecord(message.text);
        const text = String(textPayload?.body ?? "").trim();
        const sender = normalizePhoneNumber(String(message.from ?? ""));
        const externalMessageId = String(message.id ?? "");
        if (!sender || !text || !externalMessageId) continue;
        result.push({
          sender,
          phoneNumberId,
          externalMessageId,
          text,
          timestamp: message.timestamp == null
            ? null
            : String(message.timestamp),
        });
      }
    }
  }
  return result;
}

function limitedText(value: unknown, maximum = 500): string {
  return String(value ?? "").slice(0, maximum);
}

export function parseWebhookStatuses(
  payload: unknown,
): WhatsAppDeliveryStatus[] {
  const root = asRecord(payload);
  if (!root) return [];

  const result: WhatsAppDeliveryStatus[] = [];
  for (const rawEntry of asArray(root.entry)) {
    const entry = asRecord(rawEntry);
    if (!entry) continue;
    for (const rawChange of asArray(entry.changes)) {
      const change = asRecord(rawChange);
      if (!change || change.field !== "messages") continue;
      const value = asRecord(change.value);
      const metadata = value ? asRecord(value.metadata) : null;
      const phoneNumberId = String(metadata?.phone_number_id ?? "");
      if (!value || !phoneNumberId) continue;

      for (const rawStatus of asArray(value.statuses)) {
        const status = asRecord(rawStatus);
        if (!status) continue;
        const providerMessageId = String(status.id ?? "");
        const recipientId = normalizePhoneNumber(
          String(status.recipient_id ?? ""),
        );
        const statusName = String(status.status ?? "");
        if (!providerMessageId || !recipientId || !statusName) continue;

        const errors = asArray(status.errors).flatMap((rawError) => {
          const error = asRecord(rawError);
          if (!error) return [];
          const errorData = asRecord(error.error_data);
          return [{
            code: limitedText(error.code, 50),
            title: limitedText(error.title),
            message: limitedText(error.message),
            details: limitedText(errorData?.details, 1000),
          }];
        });
        result.push({
          phoneNumberId,
          providerMessageId,
          recipientId,
          status: statusName,
          timestamp: status.timestamp == null ? null : String(status.timestamp),
          errors,
        });
      }
    }
  }
  return result;
}

function hexToBytes(value: string): Uint8Array | null {
  if (!/^[0-9a-f]{64}$/i.test(value)) return null;
  const bytes = new Uint8Array(value.length / 2);
  for (let index = 0; index < value.length; index += 2) {
    bytes[index / 2] = Number.parseInt(value.slice(index, index + 2), 16);
  }
  return bytes;
}

export async function verifyWebhookSignature(
  body: Uint8Array,
  signature: string | null,
  appSecret: string,
): Promise<boolean> {
  if (!appSecret || !signature?.startsWith("sha256=")) return false;
  const supplied = hexToBytes(signature.slice("sha256=".length));
  if (!supplied) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(appSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  return crypto.subtle.verify(
    "HMAC",
    key,
    supplied.buffer as ArrayBuffer,
    body.buffer as ArrayBuffer,
  );
}

export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return Array.from(
    new Uint8Array(digest),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function secureTextEquals(
  left: string,
  right: string,
): Promise<boolean> {
  if (!left || !right) return false;
  const [leftHash, rightHash] = await Promise.all([
    sha256Hex(left),
    sha256Hex(right),
  ]);
  return leftHash === rightHash;
}
