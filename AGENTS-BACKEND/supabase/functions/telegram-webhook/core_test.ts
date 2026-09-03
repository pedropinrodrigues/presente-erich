import {
  extractTelegramInviteToken,
  extractTelegramVerificationCode,
  isTelegramStartCommand,
  parseTelegramUpdate,
  secureTextEquals,
  TELEGRAM_INVITE_REQUIRED_TEXT,
  TELEGRAM_ONBOARDING_TEXT,
  TELEGRAM_WELCOME_BACK_TEXT,
} from "./core.ts";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(message);
}

Deno.test("parses a private Telegram text message", () => {
  const messages = parseTelegramUpdate({
    update_id: 10,
    message: {
      message_id: 42,
      date: 1786881600,
      chat: { id: 123456789, type: "private" },
      from: { id: 123456789, is_bot: false },
      text: "  Olá  ",
    },
  });
  assert(messages.length === 1, "one message expected");
  assert(messages[0].chatId === "123456789", "chat id");
  assert(messages[0].externalMessageId === "123456789:42", "message id");
  assert(messages[0].text === "Olá", "trimmed text");
});

Deno.test("parses a private Telegram voice message", () => {
  const messages = parseTelegramUpdate({
    message: {
      message_id: 43,
      date: 1786881600,
      chat: { id: 123456789, type: "private" },
      from: { id: 123456789, is_bot: false },
      voice: {
        file_id: "telegram-file",
        file_unique_id: "unique-file",
        duration: 12,
        mime_type: "audio/ogg",
        file_size: 2048,
      },
    },
  });
  assert(messages.length === 1, "one voice message expected");
  assert(messages[0].text === "", "empty text");
  assert(messages[0].voice?.fileId === "telegram-file", "voice file id");
  assert(messages[0].voice?.durationSeconds === 12, "voice duration");
});

Deno.test("parses an attached Telegram audio file", () => {
  const messages = parseTelegramUpdate({
    message: {
      message_id: 44,
      chat: { id: 123456789, type: "private" },
      from: { id: 123456789, is_bot: false },
      audio: {
        file_id: "audio-file",
        file_unique_id: "unique-audio",
        duration: 30,
        mime_type: "audio/mpeg",
      },
    },
  });
  assert(messages.length === 1, "one audio message expected");
  assert(messages[0].voice?.fileId === "audio-file", "audio file id");
});

Deno.test("extracts invitation payload without treating arbitrary text as an invite", () => {
  assert(
    extractTelegramInviteToken("/start invite_abc_DEF-123") === "abc_DEF-123",
    "invite token",
  );
  assert(
    extractTelegramInviteToken("/start@test_bot invite_token") === "token",
    "addressed invite",
  );
  assert(extractTelegramInviteToken("/start binding-code") === null, "binding");
  assert(
    extractTelegramInviteToken("hello invite_token") === null,
    "plain text",
  );
  assert(isTelegramStartCommand("/start"), "plain start");
});

Deno.test("ignores groups, bots and non-text messages", () => {
  assert(
    parseTelegramUpdate({
      message: {
        message_id: 1,
        chat: { id: -1, type: "group" },
        from: { id: 2, is_bot: false },
        text: "Olá",
      },
    }).length === 0,
    "group",
  );
  assert(
    parseTelegramUpdate({
      message: {
        message_id: 1,
        chat: { id: 1, type: "private" },
        from: { id: 2, is_bot: true },
        text: "Olá",
      },
    }).length === 0,
    "bot",
  );
});

Deno.test("extracts deep-link and manual verification codes", () => {
  assert(
    extractTelegramVerificationCode("/start abc_DEF-123") === "abc_DEF-123",
    "start",
  );
  assert(
    extractTelegramVerificationCode("/start@test_agent_bot code") === "code",
    "addressed start",
  );
  assert(extractTelegramVerificationCode("VINCULAR code") === "code", "manual");
  assert(extractTelegramVerificationCode("/start") === null, "missing code");
});

Deno.test("compares webhook secrets", async () => {
  assert(await secureTextEquals("secret", "secret"), "same secret");
  assert(!(await secureTextEquals("secret", "wrong")), "different secret");
});

Deno.test("onboarding teaches natural language and essential commands", () => {
  assert(TELEGRAM_ONBOARDING_TEXT.includes("Bem-vindo à Luna"), "welcome");
  assert(
    TELEGRAM_ONBOARDING_TEXT.includes("não precisa decorar comandos"),
    "natural language",
  );
  assert(TELEGRAM_ONBOARDING_TEXT.includes("/ajuda"), "help command");
  assert(
    TELEGRAM_ONBOARDING_TEXT.includes("/macwhisper"),
    "MacWhisper command",
  );
  assert(
    !TELEGRAM_ONBOARDING_TEXT.includes("/convidar"),
    "admin commands omitted",
  );
  assert(TELEGRAM_WELCOME_BACK_TEXT.includes("/ajuda"), "returning user help");
  assert(
    TELEGRAM_INVITE_REQUIRED_TEXT.includes("convite válido"),
    "invite guidance",
  );
});
