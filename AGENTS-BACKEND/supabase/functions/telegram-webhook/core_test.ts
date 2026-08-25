import {
  extractTelegramInviteToken,
  extractTelegramVerificationCode,
  isTelegramStartCommand,
  parseTelegramUpdate,
  secureTextEquals,
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
  assert(extractTelegramInviteToken("hello invite_token") === null, "plain text");
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
