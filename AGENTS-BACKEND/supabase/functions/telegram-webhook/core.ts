const encoder = new TextEncoder();

export type TelegramInboundText = {
  chatId: string;
  userId: string;
  externalMessageId: string;
  text: string;
  timestamp: string | null;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

export function parseTelegramUpdate(payload: unknown): TelegramInboundText[] {
  const root = asRecord(payload);
  const message = root ? asRecord(root.message) : null;
  const chat = message ? asRecord(message.chat) : null;
  const sender = message ? asRecord(message.from) : null;
  if (!message || !chat || !sender) return [];
  if (chat.type !== "private" || sender.is_bot === true) return [];

  const chatId = String(chat.id ?? "");
  const userId = String(sender.id ?? "");
  const messageId = String(message.message_id ?? "");
  const text = String(message.text ?? "").trim();
  if (!chatId || !userId || !messageId || !text) return [];
  return [{
    chatId,
    userId,
    externalMessageId: `${chatId}:${messageId}`,
    text,
    timestamp: message.date == null ? null : String(message.date),
  }];
}

export function extractTelegramVerificationCode(text: string): string | null {
  const parts = text.trim().split(/\s+/, 2);
  if (parts.length !== 2) return null;
  const command = parts[0].split("@", 1)[0].toLocaleLowerCase("pt-BR");
  const code = parts[1];
  if (!["/start", "/vincular", "vincular"].includes(command)) return null;
  return code && code.length <= 100 ? code : null;
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
