from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agents_backend.api.main import app
from agents_backend.config import Settings


def test_health_is_public_and_safe() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_missing_required_configuration_fails_clearly() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)  # type: ignore[call-arg]
    text = str(error.value)
    assert "SUPABASE_URL" in text
    assert "OPENAI_API_KEY" in text


def test_private_route_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/v1/memory/search")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_agent_and_channel_binding_require_authentication() -> None:
    with TestClient(app) as client:
        agent = client.post(
            "/v1/agent/turns",
            json={"message_id": "unauthorized", "message": "Olá"},
        )
        binding = client.post(
            "/v1/channels/whatsapp/accounts",
            json={"phone_number": "+55 11 99999-9999"},
        )
        telegram_binding = client.post("/v1/channels/telegram/accounts", json={})
    assert agent.status_code == 401
    assert binding.status_code == 401
    assert telegram_binding.status_code == 401


def test_whatsapp_webhook_rejects_missing_signature() -> None:
    with TestClient(app) as client:
        response = client.post("/webhooks/whatsapp", json={"entry": []})
    assert response.status_code == 401


def test_telegram_webhook_rejects_missing_secret() -> None:
    with TestClient(app) as client:
        response = client.post("/webhooks/telegram", json={"update_id": 1})
    assert response.status_code == 401
