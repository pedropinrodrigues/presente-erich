export async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function callbackState(request: Request): string | null {
  const value = new URL(request.url).searchParams.get("state");
  return value && value.length >= 32 && value.length <= 200 ? value : null;
}

export function html(message: string, ok: boolean): Response {
  const color = ok ? "#166534" : "#991b1b";
  const body = `<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Integração</title><body style="font:16px system-ui;max-width:620px;margin:80px auto;padding:24px;color:${color}"><h1>${ok ? "Conta conectada" : "Não foi possível conectar"}</h1><p>${message}</p><p>Você já pode fechar esta janela e voltar ao chat.</p></body></html>`;
  return new Response(body, { status: ok ? 200 : 400, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}
