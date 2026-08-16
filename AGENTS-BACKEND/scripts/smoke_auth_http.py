from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete

from agents_backend.api.main import app
from agents_backend.config import get_settings
from agents_backend.db import get_session_factory
from agents_backend.models import Workspace


async def smoke_auth_http() -> None:
    settings = get_settings()
    email = f"agents-smoke-{uuid.uuid4().hex}@example.com"
    password = f"Smoke-{uuid.uuid4().hex}!aA1"
    service_key = settings.supabase_service_role_key.get_secret_value()
    anon_key = settings.supabase_anon_key.get_secret_value()
    user_id: uuid.UUID | None = None
    async with httpx.AsyncClient(timeout=30) as supabase:
        create_response = await supabase.post(
            f"{settings.supabase_url}/auth/v1/admin/users",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            json={"email": email, "password": password, "email_confirm": True},
        )
        create_response.raise_for_status()
        user_id = uuid.UUID(create_response.json()["id"])
        try:
            token_response = await supabase.post(
                f"{settings.supabase_url}/auth/v1/token",
                params={"grant_type": "password"},
                headers={"apikey": anon_key},
                json={"email": email, "password": password},
            )
            token_response.raise_for_status()
            access_token = token_response.json()["access_token"]
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                payload = {
                    "capture_id": str(uuid.uuid4()),
                    "source": "synthetic-auth-smoke",
                    "captured_at": datetime.now(UTC).isoformat(),
                    "transcript": "Fonte sintética para validar autenticação e ingestão.",
                    "language": "pt-BR",
                    "metadata": {"smoke_test": True},
                }
                headers = {"Authorization": f"Bearer {access_token}"}
                first = await client.post("/v1/transcripts", headers=headers, json=payload)
                assert first.status_code == 201, first.text
                replay = await client.post("/v1/transcripts", headers=headers, json=payload)
                assert replay.status_code == 200, replay.text
                assert first.json()["source_id"] == replay.json()["source_id"]
                source = await client.get(
                    f"/v1/sources/{first.json()['source_id']}", headers=headers
                )
                assert source.status_code == 200, source.text
                print("smoke_auth_http_ok", first.status_code, replay.status_code)
        finally:
            if user_id is not None:
                async with get_session_factory()() as session:
                    await session.execute(
                        delete(Workspace).where(Workspace.owner_user_id == user_id)
                    )
                    await session.commit()
                await supabase.delete(
                    f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                    headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
                )


if __name__ == "__main__":
    asyncio.run(smoke_auth_http())
