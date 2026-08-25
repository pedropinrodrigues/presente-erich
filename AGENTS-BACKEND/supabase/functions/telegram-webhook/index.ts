import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2";

import {
  extractTelegramInviteToken,
  extractTelegramVerificationCode,
  isTelegramStartCommand,
  parseTelegramUpdate,
  secureTextEquals,
  sha256Hex,
  type TelegramInboundText,
} from "./core.ts";

const PROVIDER = "telegram";
const decoder = new TextDecoder();

type DatabaseClient = SupabaseClient;
type ChannelAccount = { id: string; workspace_id: string; user_id: string };
type Conversation = { id: string };
type InviteAcceptance = {
  result_code: string;
  resolved_user_id: string | null;
  resolved_workspace_id: string | null;
  resolved_channel_account_id: string | null;
};

function jsonResponse(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function requiredEnvironment(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

function databaseClient(): DatabaseClient {
  return createClient(
    requiredEnvironment("SUPABASE_URL"),
    requiredEnvironment("SUPABASE_SERVICE_ROLE_KEY"),
    { auth: { autoRefreshToken: false, persistSession: false } },
  );
}

async function activatePendingAccount(
  client: DatabaseClient,
  chatId: string,
  text: string,
): Promise<boolean> {
  const code = extractTelegramVerificationCode(text);
  if (!code) return false;
  const now = new Date().toISOString();
  const suppliedHash = await sha256Hex(code.toLocaleLowerCase("pt-BR"));
  const { data: account, error: selectError } = await client
    .from("channel_accounts")
    .select("id")
    .eq("provider", PROVIDER)
    .eq("active", false)
    .gt("verification_expires_at", now)
    .eq("verification_code_hash", suppliedHash)
    .limit(1)
    .maybeSingle<{ id: string }>();
  if (selectError) throw selectError;
  if (!account) return false;

  const { error: updateError } = await client
    .from("channel_accounts")
    .update({
      external_account_id: chatId,
      active: true,
      verified_at: now,
      verification_code_hash: null,
      verification_expires_at: null,
      updated_at: now,
    })
    .eq("id", account.id)
    .eq("active", false);
  if (updateError) throw updateError;
  return true;
}

async function activeAccount(
  client: DatabaseClient,
  chatId: string,
): Promise<ChannelAccount | null> {
  const { data, error } = await client
    .from("channel_accounts")
    .select("id,workspace_id,user_id")
    .eq("provider", PROVIDER)
    .eq("external_account_id", chatId)
    .eq("active", true)
    .limit(1)
    .maybeSingle<ChannelAccount>();
  if (error) throw error;
  return data;
}

async function acceptInvite(
  client: DatabaseClient,
  message: TelegramInboundText,
  token: string,
): Promise<string> {
  const { data, error } = await client.rpc("accept_telegram_invite", {
    p_token_hash: await sha256Hex(token),
    p_chat_id: message.chatId,
    p_telegram_user_id: message.userId,
    p_profile_metadata: {
      first_name: message.firstName,
      language_code: message.languageCode,
    },
  });
  if (error) throw error;
  const row = (Array.isArray(data) ? data[0] : data) as InviteAcceptance | null;
  return row?.result_code ?? "unavailable";
}

async function findConversation(
  client: DatabaseClient,
  chatId: string,
): Promise<Conversation | null> {
  const { data, error } = await client
    .from("conversations")
    .select("id")
    .eq("provider", PROVIDER)
    .eq("external_thread_id", chatId)
    .limit(1)
    .maybeSingle<Conversation>();
  if (error) throw error;
  return data;
}

async function resolveConversation(
  client: DatabaseClient,
  account: ChannelAccount,
  message: TelegramInboundText,
): Promise<Conversation> {
  const existing = await findConversation(client, message.chatId);
  if (existing) return existing;
  const now = new Date().toISOString();
  const conversation = {
    id: crypto.randomUUID(),
    workspace_id: account.workspace_id,
    user_id: account.user_id,
    channel_account_id: account.id,
    provider: PROVIDER,
    external_thread_id: message.chatId,
    status: "active",
    conversation_metadata: { chat_id: message.chatId },
    created_at: now,
    updated_at: now,
  };
  const { data, error } = await client
    .from("conversations")
    .insert(conversation)
    .select("id")
    .single<Conversation>();
  if (!error && data) return data;
  if (error?.code === "23505") {
    const raced = await findConversation(client, message.chatId);
    if (raced) return raced;
  }
  throw error ?? new Error("Conversation insert returned no row");
}

async function messageAlreadyExists(
  client: DatabaseClient,
  externalMessageId: string,
): Promise<boolean> {
  const { data, error } = await client
    .from("channel_messages")
    .select("id")
    .eq("provider", PROVIDER)
    .eq("external_message_id", externalMessageId)
    .limit(1)
    .maybeSingle<{ id: string }>();
  if (error) throw error;
  return data !== null;
}

async function ingestMessage(
  client: DatabaseClient,
  message: TelegramInboundText,
): Promise<"ignored" | "accepted" | "duplicate"> {
  const account = await activeAccount(client, message.chatId);
  if (!account) return "ignored";
  if (await messageAlreadyExists(client, message.externalMessageId)) {
    return "duplicate";
  }
  const conversation = await resolveConversation(client, account, message);
  const now = new Date().toISOString();
  const { error } = await client.from("channel_messages").insert({
    id: crypto.randomUUID(),
    workspace_id: account.workspace_id,
    conversation_id: conversation.id,
    reply_to_message_id: null,
    provider: PROVIDER,
    external_message_id: message.externalMessageId,
    direction: "inbound",
    content: message.text,
    status: "received",
    attempts: 0,
    max_attempts: 3,
    available_at: now,
    lease_expires_at: null,
    locked_by: null,
    error_code: null,
    message_metadata: {
      sender: message.chatId,
      chat_id: message.chatId,
      telegram_user_id: message.userId,
      provider_timestamp: message.timestamp,
    },
    created_at: now,
    updated_at: now,
  });
  if (!error) return "accepted";
  if (error.code === "23505") return "duplicate";
  throw error;
}

async function sendTelegramText(chatId: string, text: string): Promise<void> {
  const token = requiredEnvironment("TELEGRAM_BOT_TOKEN");
  const response = await fetch(
    `https://api.telegram.org/bot${token}/sendMessage`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text }),
    },
  );
  if (!response.ok) {
    throw new Error(`Telegram sendMessage failed: ${response.status}`);
  }
}

async function handleWebhook(request: Request): Promise<Response> {
  const expectedSecret = requiredEnvironment("TELEGRAM_WEBHOOK_SECRET");
  const suppliedSecret =
    request.headers.get("x-telegram-bot-api-secret-token") ?? "";
  if (!(await secureTextEquals(suppliedSecret, expectedSecret))) {
    return jsonResponse(401, { error: "invalid_webhook_secret" });
  }
  const body = new Uint8Array(await request.arrayBuffer());
  if (body.byteLength > 1024 * 1024) {
    return jsonResponse(413, { error: "webhook_payload_too_large" });
  }
  let payload: unknown;
  try {
    payload = JSON.parse(decoder.decode(body));
  } catch {
    return jsonResponse(400, { error: "invalid_webhook_payload" });
  }

  const client = databaseClient();
  let accepted = 0;
  let duplicates = 0;
  for (const message of parseTelegramUpdate(payload)) {
    const inviteToken = extractTelegramInviteToken(message.text);
    if (inviteToken) {
      const result = await acceptInvite(client, message, inviteToken);
      accepted += 1;
      try {
        if ([
          "created",
          "already_registered",
          "already_accepted_by_same_identity",
        ].includes(result)) {
          await sendTelegramText(
            message.chatId,
            result === "created"
              ? "Sua conta foi criada e este Telegram já está vinculado. Você possui um espaço pessoal separado e já pode conversar comigo."
              : "Seu Telegram já possui uma conta. Continue usando normalmente.",
          );
        } else {
          await sendTelegramText(
            message.chatId,
            "Este convite não está mais disponível. Peça um novo link à pessoa que convidou você.",
          );
        }
      } catch (error) {
        console.error(
          "telegram_invite_confirmation_failed",
          error instanceof Error ? error.message : String(error),
        );
      }
      continue;
    }
    if (extractTelegramVerificationCode(message.text)) {
      if (await activatePendingAccount(client, message.chatId, message.text)) {
        accepted += 1;
        try {
          await sendTelegramText(
            message.chatId,
            "Vínculo concluído! Envie uma mensagem para conversar com seu agente.",
          );
        } catch (error) {
          console.error(
            "telegram_binding_confirmation_failed",
            error instanceof Error ? error.message : String(error),
          );
        }
      }
      continue;
    }
    if (isTelegramStartCommand(message.text)) {
      const account = await activeAccount(client, message.chatId);
      if (!account) {
        accepted += 1;
        await sendTelegramText(
          message.chatId,
          "Para criar uma conta, abra um convite válido enviado pelo administrador.",
        );
        continue;
      }
    }
    const result = await ingestMessage(client, message);
    if (result === "accepted") accepted += 1;
    if (result === "duplicate") {
      accepted += 1;
      duplicates += 1;
    }
  }
  return jsonResponse(200, {
    status: "accepted",
    messages: accepted,
    duplicates,
  });
}

Deno.serve(async (request) => {
  try {
    if (request.method === "POST") return await handleWebhook(request);
    return jsonResponse(405, { error: "method_not_allowed" });
  } catch (error) {
    console.error(
      "telegram_webhook_failed",
      error instanceof Error ? error.message : String(error),
    );
    return jsonResponse(500, { error: "telegram_webhook_unavailable" });
  }
});
