const encoder = new TextEncoder();

export const TELEGRAM_ONBOARDING_TEXT = [
  "Bem-vindo à Luna! 👋",
  "",
  "Sua conta pessoal está pronta e este Telegram já está conectado.",
  "",
  "Você pode escrever ou enviar áudio normalmente — não precisa decorar comandos. Experimente:",
  "• Guarde que a reunião do Projeto Atlas ficou para sexta.",
  "• Quais são minhas pendências?",
  "• Pesquise na internet as novidades sobre um assunto.",
  "• Amanhã às 9h, me lembre de ligar para a Marina.",
  "",
  "Quer usar o MacWhisper? Envie /macwhisper, copie a URL pessoal e siga as instruções recebidas.",
  "",
  "Comandos úteis:",
  "• /ajuda — veja capacidades e comandos.",
  "• /minhaconta — confira sua conta.",
  "• /macwhisper — configure transcrições.",
  "• /revogarmacwhisper — invalide a URL anterior.",
  "",
  "Quando tiver dúvida, diga o que deseja fazer e eu explico o próximo passo.",
].join("\n");

export const TELEGRAM_WELCOME_BACK_TEXT = [
  "Seu Telegram já possui uma conta e continua conectado à Luna.",
  "Envie uma mensagem ou áudio normalmente. Use /ajuda quando quiser rever possibilidades e comandos.",
].join("\n\n");

export const TELEGRAM_INVITE_REQUIRED_TEXT = [
  "Olá! Para criar sua conta pessoal na Luna, abra um convite válido enviado pelo administrador.",
  "Se você já recebeu o link, abra-o e toque em Iniciar. Se ele expirou, peça um novo convite.",
].join("\n\n");

export type TelegramInboundVoice = {
  fileId: string;
  fileUniqueId: string;
  durationSeconds: number;
  mimeType: string | null;
  fileSizeBytes: number | null;
};

export type TelegramInboundMessage = {
  chatId: string;
  userId: string;
  externalMessageId: string;
  text: string;
  timestamp: string | null;
  firstName: string | null;
  languageCode: string | null;
  voice: TelegramInboundVoice | null;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

export function parseTelegramUpdate(
  payload: unknown,
): TelegramInboundMessage[] {
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
  const rawVoice = asRecord(message.voice) ?? asRecord(message.audio);
  const voiceFileId = String(rawVoice?.file_id ?? "");
  const voiceUniqueId = String(rawVoice?.file_unique_id ?? "");
  const voiceDuration = rawVoice?.duration;
  const voice =
    voiceFileId && voiceUniqueId && typeof voiceDuration === "number"
      ? {
        fileId: voiceFileId,
        fileUniqueId: voiceUniqueId,
        durationSeconds: voiceDuration,
        mimeType: rawVoice?.mime_type == null
          ? null
          : String(rawVoice.mime_type),
        fileSizeBytes: typeof rawVoice?.file_size === "number"
          ? rawVoice.file_size
          : null,
      }
      : null;
  if (!chatId || !userId || !messageId || (!text && !voice)) return [];
  return [{
    chatId,
    userId,
    externalMessageId: `${chatId}:${messageId}`,
    text,
    timestamp: message.date == null ? null : String(message.date),
    firstName: sender.first_name == null ? null : String(sender.first_name),
    languageCode: sender.language_code == null
      ? null
      : String(sender.language_code),
    voice,
  }];
}

export function extractTelegramInviteToken(text: string): string | null {
  const parts = text.trim().split(/\s+/, 2);
  if (parts.length !== 2) return null;
  const command = parts[0].split("@", 1)[0].toLocaleLowerCase("pt-BR");
  const payload = parts[1];
  if (command !== "/start" || !payload.startsWith("invite_")) return null;
  const token = payload.slice("invite_".length);
  return token && token.length <= 100 && /^[A-Za-z0-9_-]+$/.test(token)
    ? token
    : null;
}

export function isTelegramStartCommand(text: string): boolean {
  const command = text.trim().split(/\s+/, 1)[0].split("@", 1)[0]
    .toLocaleLowerCase("pt-BR");
  return command === "/start";
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
