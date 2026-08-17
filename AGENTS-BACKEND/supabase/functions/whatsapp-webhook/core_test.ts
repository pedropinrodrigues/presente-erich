import {
  normalizePhoneNumber,
  parseWebhookMessages,
  parseWebhookStatuses,
  phoneNumbersEquivalent,
  secureTextEquals,
  verifyWebhookSignature,
  whatsappPhoneAliases,
} from "./core.ts";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(message);
}

Deno.test("normalizes a WhatsApp phone number", () => {
  assert(
    normalizePhoneNumber("+55 (11) 99999-9999") === "5511999999999",
    "normalization",
  );
  assert(normalizePhoneNumber("123") === null, "short numbers must fail");
});

Deno.test("matches Brazilian WhatsApp numbers with or without the ninth digit", () => {
  const withNinthDigit = "5584998765432";
  const providerWaId = "558498765432";
  assert(
    whatsappPhoneAliases(withNinthDigit).includes(providerWaId),
    "provider alias",
  );
  assert(
    whatsappPhoneAliases(providerWaId).includes(withNinthDigit),
    "user alias",
  );
  assert(
    phoneNumbersEquivalent(withNinthDigit, providerWaId),
    "equivalent numbers",
  );
});

Deno.test("parses only complete text messages", () => {
  const result = parseWebhookMessages({
    entry: [
      {
        changes: [
          {
            field: "messages",
            value: {
              metadata: { phone_number_id: "phone-id" },
              messages: [
                {
                  from: "+55 11 99999-9999",
                  id: "wamid.1",
                  timestamp: "1234",
                  type: "text",
                  text: { body: "  olá  " },
                },
                { from: "5511999999999", id: "wamid.2", type: "image" },
              ],
            },
          },
        ],
      },
    ],
  });
  assert(result.length === 1, "one message expected");
  assert(result[0].sender === "5511999999999", "sender");
  assert(result[0].phoneNumberId === "phone-id", "phone number id");
  assert(result[0].externalMessageId === "wamid.1", "external id");
  assert(result[0].text === "olá", "text");
});

Deno.test("parses delivery status errors without message content", () => {
  const result = parseWebhookStatuses({
    entry: [{
      changes: [{
        field: "messages",
        value: {
          metadata: { phone_number_id: "phone-id" },
          statuses: [{
            id: "wamid.outbound",
            recipient_id: "+55 11 99999-9999",
            status: "failed",
            timestamp: "1234",
            errors: [{
              code: 131026,
              title: "Message undeliverable",
              message: "Message undeliverable",
              error_data: { details: "The destination could not be reached" },
            }],
          }],
        },
      }],
    }],
  });
  assert(result.length === 1, "one status expected");
  assert(result[0].recipientId === "5511999999999", "recipient");
  assert(result[0].status === "failed", "status");
  assert(result[0].errors[0].code === "131026", "error code");
});

Deno.test("validates the Meta HMAC signature", async () => {
  const secret = "test-app-secret";
  const body = new TextEncoder().encode('{"entry":[]}');
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const digest = await crypto.subtle.sign("HMAC", key, body);
  const hex = Array.from(
    new Uint8Array(digest),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  assert(
    await verifyWebhookSignature(body, `sha256=${hex}`, secret),
    "valid signature",
  );
  assert(
    !(await verifyWebhookSignature(body, `sha256=${"0".repeat(64)}`, secret)),
    "invalid",
  );
});

Deno.test("compares verification tokens", async () => {
  assert(await secureTextEquals("token", "token"), "equal tokens");
  assert(!(await secureTextEquals("token", "different")), "different tokens");
});
