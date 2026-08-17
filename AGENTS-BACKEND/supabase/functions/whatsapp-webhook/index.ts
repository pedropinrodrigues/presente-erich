import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2";

import {
  parseWebhookMessages,
  parseWebhookStatuses,
  secureTextEquals,
  sha256Hex,
  verifyWebhookSignature,
  type WhatsAppDeliveryStatus,
  type WhatsAppInboundText,
  whatsappPhoneAliases,
} from "./core.ts";

const PROVIDER = "meta_whatsapp";
const decoder = new TextDecoder();

type DatabaseClient = SupabaseClient;

type ChannelAccount = {
  id: string;
  workspace_id: string;
  user_id: string;
};

type Conversation = {
  id: string;
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
    {
      auth: { autoRefreshToken: false, persistSession: false },
    },
  );
}

async function activatePendingAccount(
  client: DatabaseClient,
  sender: string,
  text: string,
): Promise<boolean> {
  const now = new Date().toISOString();
  const suppliedHash = await sha256Hex(text.trim().toLocaleLowerCase("pt-BR"));
  const { data: account, error: selectError } = await client
    .from("channel_accounts")
    .select("id")
    .eq("provider", PROVIDER)
    .in("external_account_id", whatsappPhoneAliases(sender))
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
  sender: string,
): Promise<ChannelAccount | null> {
  const { data, error } = await client
    .from("channel_accounts")
    .select("id,workspace_id,user_id")
    .eq("provider", PROVIDER)
    .in("external_account_id", whatsappPhoneAliases(sender))
    .eq("active", true)
    .limit(1)
    .maybeSingle<ChannelAccount>();
  if (error) throw error;
  return data;
}

async function findConversation(
  client: DatabaseClient,
  externalThreadId: string,
): Promise<Conversation | null> {
  const { data, error } = await client
    .from("conversations")
    .select("id")
    .eq("provider", PROVIDER)
    .eq("external_thread_id", externalThreadId)
    .limit(1)
    .maybeSingle<Conversation>();
  if (error) throw error;
  return data;
}

async function resolveConversation(
  client: DatabaseClient,
  account: ChannelAccount,
  message: WhatsAppInboundText,
): Promise<Conversation> {
  const externalThreadId = `${message.phoneNumberId}:${message.sender}`;
  const existing = await findConversation(client, externalThreadId);
  if (existing) return existing;

  const now = new Date().toISOString();
  const conversation = {
    id: crypto.randomUUID(),
    workspace_id: account.workspace_id,
    user_id: account.user_id,
    channel_account_id: account.id,
    provider: PROVIDER,
    external_thread_id: externalThreadId,
    status: "active",
    conversation_metadata: { phone_number_id: message.phoneNumberId },
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
    const raced = await findConversation(client, externalThreadId);
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
  message: WhatsAppInboundText,
): Promise<"ignored" | "accepted" | "duplicate"> {
  const account = await activeAccount(client, message.sender);
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
      sender: message.sender,
      phone_number_id: message.phoneNumberId,
      provider_timestamp: message.timestamp,
    },
    created_at: now,
    updated_at: now,
  });
  if (!error) return "accepted";
  if (error.code === "23505") return "duplicate";
  throw error;
}

async function persistDeliveryStatus(
  client: DatabaseClient,
  status: WhatsAppDeliveryStatus,
): Promise<boolean> {
  const { data: account, error: accountError } = await client
    .from("channel_accounts")
    .select("id,workspace_id")
    .eq("provider", PROVIDER)
    .in("external_account_id", whatsappPhoneAliases(status.recipientId))
    .limit(1)
    .maybeSingle<{ id: string; workspace_id: string }>();
  if (accountError) throw accountError;
  if (!account) return false;

  const { data: message, error: messageError } = await client
    .from("channel_messages")
    .select("id,message_metadata")
    .eq("provider", PROVIDER)
    .eq("external_message_id", status.providerMessageId)
    .eq("direction", "outbound")
    .limit(1)
    .maybeSingle<
      { id: string; message_metadata: Record<string, unknown> | null }
    >();
  if (messageError) throw messageError;

  const now = new Date().toISOString();
  if (message) {
    const { error: updateError } = await client
      .from("channel_messages")
      .update({
        status: status.status === "failed" ? "failed" : "completed",
        error_code: status.status === "failed"
          ? "whatsapp_delivery_failed"
          : null,
        message_metadata: {
          ...(message.message_metadata ?? {}),
          delivery_status: {
            status: status.status,
            timestamp: status.timestamp,
            errors: status.errors,
          },
        },
        updated_at: now,
      })
      .eq("id", message.id);
    if (updateError) throw updateError;
  }

  const firstError = status.errors[0];
  const reason = firstError
    ? (firstError.details || firstError.message || firstError.title).slice(
      0,
      500,
    )
    : null;
  const { error: auditError } = await client.from("audit_events").insert({
    id: crypto.randomUUID(),
    workspace_id: account.workspace_id,
    actor_user_id: null,
    operation: "whatsapp_delivery_status",
    target_type: "channel_account",
    target_id: account.id,
    reason,
    event_metadata: {
      status: status.status,
      provider_message_id: status.providerMessageId,
      provider_timestamp: status.timestamp,
      errors: status.errors,
    },
    created_at: now,
  });
  if (auditError) throw auditError;
  return true;
}

async function handleVerification(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const mode = url.searchParams.get("hub.mode");
  const token = url.searchParams.get("hub.verify_token") ?? "";
  const challenge = url.searchParams.get("hub.challenge");
  const expected = requiredEnvironment("WHATSAPP_VERIFY_TOKEN");
  if (
    mode !== "subscribe" || !challenge ||
    !(await secureTextEquals(token, expected))
  ) {
    return jsonResponse(401, { error: "invalid_webhook_verification" });
  }
  return new Response(challenge, {
    status: 200,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}

async function handleWebhook(request: Request): Promise<Response> {
  const body = new Uint8Array(await request.arrayBuffer());
  if (body.byteLength > 1024 * 1024) {
    return jsonResponse(413, { error: "webhook_payload_too_large" });
  }
  const signature = request.headers.get("x-hub-signature-256");
  const appSecret = requiredEnvironment("WHATSAPP_APP_SECRET");
  if (!(await verifyWebhookSignature(body, signature, appSecret))) {
    return jsonResponse(401, { error: "invalid_webhook_signature" });
  }

  let payload: unknown;
  try {
    payload = JSON.parse(decoder.decode(body));
  } catch {
    return jsonResponse(400, { error: "invalid_webhook_payload" });
  }

  const configuredPhoneNumberId = Deno.env.get("WHATSAPP_PHONE_NUMBER_ID");
  const messages = parseWebhookMessages(payload).filter(
    (message) =>
      !configuredPhoneNumberId ||
      message.phoneNumberId === configuredPhoneNumberId,
  );
  const statuses = parseWebhookStatuses(payload).filter(
    (status) =>
      !configuredPhoneNumberId ||
      status.phoneNumberId === configuredPhoneNumberId,
  );
  const client = databaseClient();
  let accepted = 0;
  let duplicates = 0;
  let deliveryStatuses = 0;

  for (const message of messages) {
    if (message.text.toLocaleLowerCase("pt-BR").startsWith("vincular ")) {
      if (await activatePendingAccount(client, message.sender, message.text)) {
        accepted += 1;
      }
      continue;
    }
    const result = await ingestMessage(client, message);
    if (result === "accepted") accepted += 1;
    if (result === "duplicate") {
      accepted += 1;
      duplicates += 1;
    }
  }
  for (const deliveryStatus of statuses) {
    if (await persistDeliveryStatus(client, deliveryStatus)) {
      deliveryStatuses += 1;
    }
  }
  return jsonResponse(202, {
    status: "accepted",
    messages: accepted,
    duplicates,
    delivery_statuses: deliveryStatuses,
  });
}

Deno.serve(async (request) => {
  try {
    if (request.method === "GET") return await handleVerification(request);
    if (request.method === "POST") return await handleWebhook(request);
    return jsonResponse(405, { error: "method_not_allowed" });
  } catch (error) {
    console.error(
      "whatsapp_webhook_failed",
      error instanceof Error ? error.message : String(error),
    );
    return jsonResponse(500, { error: "whatsapp_webhook_unavailable" });
  }
});
