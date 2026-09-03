from __future__ import annotations

# ruff: noqa: E501 -- HTML/CSS/JS is intentionally kept as a single auditable response template.
import json
import re
import secrets
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from agents_backend.api.dependencies import SessionDependency
from agents_backend.config import get_settings
from agents_backend.integrations.bitrix24.service import submit_connection_token

router = APIRouter()


def _security_headers(nonce: str | None = None) -> dict[str, str]:
    script = f"'nonce-{nonce}'" if nonce else "'none'"
    return {
        "Cache-Control": "no-store, max-age=0",
        "Content-Security-Policy": (
            "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            f"script-src {script}; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


@router.get("/connect/bitrix24", include_in_schema=False)
async def bitrix24_connection_page() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    content = f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Conectar Bitrix24</title><style>
body{{font-family:system-ui,sans-serif;background:#f6f7f9;color:#172033;margin:0;padding:32px 16px}}
main{{max-width:520px;margin:5vh auto;background:white;padding:28px;border-radius:16px;box-shadow:0 8px 28px #0001}}
label,input,button{{display:block;width:100%;box-sizing:border-box}}input{{margin:8px 0 18px;padding:12px;border:1px solid #b7bec9;border-radius:8px}}
button{{padding:12px;border:0;border-radius:8px;background:#1769e0;color:white;font-weight:650;cursor:pointer}}small{{color:#596274}}#status{{margin-top:18px}}
</style></head><body><main><h1>Conectar Bitrix24</h1>
<p>Cole o token gerado em <strong>Aplicativos → Conexões MCP</strong> no seu Bitrix24.</p>
<form id="form"><label for="token">Token de conexão</label><input id="token" name="token" type="password" minlength="8" maxlength="5000" autocomplete="off" required>
<button type="submit">Validar token</button></form><p id="status" role="status"></p>
<small>O token é enviado somente ao backend e validado no endpoint MCP oficial.</small></main>
<script nonce="{nonce}">const form=document.getElementById('form'),status=document.getElementById('status');
const state=new URLSearchParams(location.hash.slice(1)).get('state');history.replaceState(null,'',location.pathname);
form.addEventListener('submit',async(e)=>{{e.preventDefault();status.textContent='Validando…';const token=document.getElementById('token').value;
try{{const response=await fetch('/connect/bitrix24',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{state,connection_token:token}})}});const data=await response.json();status.textContent=data.message;form.hidden=data.ok;document.getElementById('token').value='';}}catch(_){{status.textContent='Não foi possível validar agora. Tente novamente.';}}}});</script>
</body></html>"""
    return HTMLResponse(content, headers=_security_headers(nonce))


@router.post("/connect/bitrix24", include_in_schema=False)
async def submit_bitrix24_connection(request: Request, session: SessionDependency) -> JSONResponse:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        return JSONResponse(
            {"ok": False, "message": "Formato de envio inválido."},
            status_code=415,
            headers=_security_headers(),
        )
    body = await request.body()
    if len(body) > 12_000:
        return JSONResponse(
            {"ok": False, "message": "Conteúdo muito grande."},
            status_code=413,
            headers=_security_headers(),
        )
    try:
        payload: Any = json.loads(body)
    except Exception:
        payload = None
    state = payload.get("state") if isinstance(payload, dict) else None
    token = payload.get("connection_token") if isinstance(payload, dict) else None
    if isinstance(token, str):
        token = token.strip()
    if (
        not isinstance(state, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,100}", state)
        or not isinstance(token, str)
        or not 8 <= len(token) <= 5000
    ):
        return JSONResponse(
            {"ok": False, "message": "Link ou token inválido."},
            status_code=400,
            headers=_security_headers(),
        )
    ok, message = await submit_connection_token(session, get_settings(), state, token)
    await session.commit()
    return JSONResponse(
        {"ok": ok, "message": message},
        status_code=200 if ok else 400,
        headers=_security_headers(),
    )
