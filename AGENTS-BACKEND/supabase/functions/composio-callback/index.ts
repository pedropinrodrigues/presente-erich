import { createClient } from "npm:@supabase/supabase-js@2";
import { callbackState, html, sha256Hex } from "./core.ts";

function required(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}

Deno.serve(async (request) => {
  if (request.method !== "GET") return new Response("Method not allowed", { status: 405 });
  const state = callbackState(request);
  if (!state) return html("O link é inválido ou incompleto.", false);
  try {
    const client = createClient(required("SUPABASE_URL"), required("SUPABASE_SERVICE_ROLE_KEY"), { auth: { persistSession: false, autoRefreshToken: false } });
    const now = new Date().toISOString();
    const hash = await sha256Hex(state);
    const { data: connection, error } = await client.from("external_connection_requests")
      .select("id,integration_id,status,expires_at")
      .eq("callback_state_hash", hash).eq("status", "pending").gt("expires_at", now)
      .limit(1).maybeSingle<{ id: string; integration_id: string; status: string; expires_at: string }>();
    if (error) throw error;
    if (!connection) return html("Este link expirou ou já foi utilizado.", false);
    const { error: integrationError } = await client.from("external_integrations")
      .update({ status: "active", last_verified_at: now, updated_at: now })
      .eq("id", connection.integration_id).eq("status", "pending");
    if (integrationError) throw integrationError;
    const { error: requestError } = await client.from("external_connection_requests")
      .update({ status: "completed", completed_at: now }).eq("id", connection.id).eq("status", "pending");
    if (requestError) throw requestError;
    return html("A autorização foi registrada com sucesso.", true);
  } catch (error) {
    console.error("composio_callback_failed", error instanceof Error ? error.message : "unknown");
    return html("Ocorreu um erro ao registrar a autorização.", false);
  }
});
