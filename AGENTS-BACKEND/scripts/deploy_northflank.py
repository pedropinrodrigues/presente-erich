from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from sqlalchemy import func, select, text

from agents_backend.config import get_settings
from agents_backend.conversation.service import ConversationService
from agents_backend.db import get_session_factory
from agents_backend.invitations.service import is_platform_admin
from agents_backend.models import (
    AppUser,
    ChannelAccount,
    ChannelInvite,
    ChannelMessage,
    PlatformAdmin,
    UserIdentity,
    WorkerHeartbeat,
    Workspace,
)
from agents_backend.worker.health import _queue_snapshot

PROJECT_ID = "presente-erich"
API_SERVICE_ID = "agents-api-prod"
WORKER_SERVICE_ID = "agents-worker-prod"
MIGRATION_JOB_ID = "agents-db-migrate"
RUNTIME_GROUP_ID = "production-runtime"
NORTHFLANK_API_BASE = "https://api.northflank.com/v1"
PUBLIC_BUNDLE_URL = (
    "https://github.com/pedropinrodrigues/presente-erich/"
    "archive/refs/heads/main.tar.gz"
)
APP_DIRECTORY = Path(__file__).resolve().parents[1]
ENV_FILE = APP_DIRECTORY / ".env.local"
EXAMPLE_ENV_FILE = APP_DIRECTORY / ".env.example"


def _git_revision() -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git não foi encontrado")
    return subprocess.check_output(  # noqa: S603 - executable and arguments are fixed
        [git, "rev-parse", "HEAD"],
        cwd=APP_DIRECTORY,
        text=True,
    ).strip()


def _runtime_variables() -> dict[str, str]:
    defaults = {key: value or "" for key, value in dotenv_values(EXAMPLE_ENV_FILE).items()}
    local = {key: value or "" for key, value in dotenv_values(ENV_FILE).items()}
    variables: dict[str, str] = {}
    for key, default in defaults.items():
        value = local.get(key, default)
        if value:
            variables[key] = value
    variables.update(
        {
            "APP_ENV": "production",
            "DEPLOYMENT_REVISION": _git_revision(),
            "DATABASE_CONNECT_TIMEOUT_SECONDS": "10",
            "DATABASE_COMMAND_TIMEOUT_SECONDS": "30",
            "WORKER_CYCLE_TIMEOUT_SECONDS": "600",
            "WORKER_MAX_CONSECUTIVE_INFRA_FAILURES": "5",
            "WORKER_HEARTBEAT_INTERVAL_SECONDS": "30",
            "QUEUE_LAG_WARNING_SECONDS": "60",
        }
    )
    return variables


class NorthflankClient:
    def __init__(self) -> None:
        token = dotenv_values(ENV_FILE).get("NORTHFLANK_API_TOKEN")
        if not token:
            raise RuntimeError("NORTHFLANK_API_TOKEN não foi encontrado em .env.local")
        self.client = httpx.Client(
            base_url=NORTHFLANK_API_BASE,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=120,
        )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        response = self.client.request(method, path, json=payload)
        if not response.is_success:
            try:
                detail = response.json()
            except ValueError:
                detail = {"message": response.text[:500]}
            raise RuntimeError(f"Northflank {method} {path}: HTTP {response.status_code}: {detail}")
        return response.json().get("data")

    def project(self) -> dict[str, Any]:
        return self.request("GET", f"/projects/{PROJECT_ID}")

    def service(self, service_id: str) -> dict[str, Any]:
        return self.request("GET", f"/projects/{PROJECT_ID}/services/{service_id}")

    def job(self, job_id: str) -> dict[str, Any]:
        return self.request("GET", f"/projects/{PROJECT_ID}/jobs/{job_id}")

    def job_runs(self, job_id: str) -> Any:
        return self.request("GET", f"/projects/{PROJECT_ID}/jobs/{job_id}/runs")

    def put_secret_group(self) -> None:
        payload = {
            "id": RUNTIME_GROUP_ID,
            "name": "Production Runtime",
            "description": "Agents backend production runtime variables",
            "type": "secret",
            "secretType": "environment",
            "priority": 10,
            "restrictions": {"restricted": False},
            "secrets": {"variables": _runtime_variables()},
        }
        self.request("PUT", f"/projects/{PROJECT_ID}/secrets", payload)

    @staticmethod
    def _build_settings() -> dict[str, Any]:
        return {
            "storage": {"ephemeralStorage": {"storageSize": 16384}},
            "dockerfile": {
                "buildEngine": "buildkit",
                "dockerFilePath": "/AGENTS-BACKEND/Dockerfile",
                "dockerWorkDir": "/AGENTS-BACKEND",
                "buildkit": {"useCache": True, "cacheStorageSize": 32768},
            },
        }

    def create_api(self) -> None:
        payload = {
            "name": "Agents API Prod",
            "description": "FastAPI for agents backend",
            "billing": {"deploymentPlan": "nf-compute-20"},
            "deployment": {
                "instances": 1,
                "docker": {"configType": "default"},
                "storage": {"ephemeralStorage": {"storageSize": 1024}},
            },
            "ports": [
                {
                    "name": "http",
                    "internalPort": 8080,
                    "public": True,
                    "protocol": "HTTP",
                }
            ],
            "buildSource": "bundle",
            "bundleData": {"bundleUrl": PUBLIC_BUNDLE_URL, "branch": "main"},
            "buildSettings": self._build_settings(),
            "healthChecks": [
                {
                    "protocol": "HTTP",
                    "type": "readinessProbe",
                    "path": "/ready",
                    "port": 8080,
                    "initialDelaySeconds": 15,
                    "periodSeconds": 30,
                    "timeoutSeconds": 5,
                    "failureThreshold": 3,
                    "successThreshold": 1,
                }
            ],
        }
        self.request("POST", f"/projects/{PROJECT_ID}/services/combined", payload)

    def create_worker(self) -> None:
        payload = {
            "name": "Agents Worker Prod",
            "description": "Always-on agents, outbox and scheduler worker",
            "billing": {"deploymentPlan": "nf-compute-20"},
            "deployment": {
                "instances": 1,
                "docker": {
                    "configType": "customCommand",
                    "customCommand": "python -m agents_backend.worker.main",
                },
                "storage": {"ephemeralStorage": {"storageSize": 1024}},
            },
            "ports": [],
            "buildSource": "bundle",
            "bundleData": {"bundleUrl": PUBLIC_BUNDLE_URL, "branch": "main"},
            "buildSettings": self._build_settings(),
        }
        self.request("POST", f"/projects/{PROJECT_ID}/services/combined", payload)

    def create_migration_job(self) -> None:
        payload = {
            "name": "Agents DB Migrate",
            "description": "Runs Alembic migrations against the production database",
            "billing": {"deploymentPlan": "nf-compute-20"},
            "deployment": {
                "docker": {
                    "configType": "customCommand",
                    "customCommand": "alembic upgrade head",
                },
                "storage": {"ephemeralStorage": {"storageSize": 1024}},
                "internal": {
                    "id": API_SERVICE_ID,
                    "branch": "main",
                    "buildSHA": "latest",
                },
            },
            "backoffLimit": 1,
            "runOnSourceChange": "never",
            "activeDeadlineSeconds": 600,
        }
        self.request("POST", f"/projects/{PROJECT_ID}/jobs/manual", payload)

    def run_migrations(self) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/projects/{PROJECT_ID}/jobs/{MIGRATION_JOB_ID}/runs",
            {},
        )

    def start_bundle_build(self, service_id: str) -> None:
        self.request(
            "POST",
            f"/projects/{PROJECT_ID}/services/{service_id}/build",
            {"bundleUrl": PUBLIC_BUNDLE_URL, "branch": "main", "sha": _git_revision()},
        )


def _resource_summary(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": project.get("id"),
        "region": (project.get("deployment") or {}).get("region"),
        "services": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "build": (item.get("status") or {}).get("build"),
                "deployment": (item.get("status") or {}).get("deployment"),
            }
            for item in project.get("services", [])
        ],
        "jobs": [
            {"id": item.get("id"), "name": item.get("name")}
            for item in project.get("jobs", [])
        ],
    }


def _deployment_summary(client: NorthflankClient) -> dict[str, Any]:
    project = client.project()
    summary = _resource_summary(project)
    summary["services"] = []
    for item in project.get("services", []):
        detail = client.service(item["id"])
        summary["services"].append(
            {
                "id": detail.get("id"),
                "name": detail.get("name"),
                "status": detail.get("status"),
                "ports": [
                    {
                        key: port.get(key)
                        for key in ("name", "internalPort", "public", "protocol", "dns", "domains")
                        if port.get(key) is not None
                    }
                    for port in detail.get("ports", [])
                ],
            }
        )
    summary["jobs"] = []
    for item in project.get("jobs", []):
        detail = client.job(item["id"])
        summary["jobs"].append(
            {
                "id": detail.get("id"),
                "name": detail.get("name"),
                "status": detail.get("status"),
            }
        )
    return summary


async def _database_summary() -> dict[str, Any]:
    async with get_session_factory()() as session:
        heartbeats = (
            await session.execute(
                select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(10)
            )
        ).scalars()
        return {
            "queues": await _queue_snapshot(session, datetime.now(UTC)),
            "workers": [
                {
                    "worker_id": heartbeat.worker_id,
                    "status": heartbeat.status,
                    "deployment_revision": heartbeat.deployment_revision,
                    "consecutive_infra_failures": heartbeat.consecutive_infra_failures,
                    "last_seen_at": heartbeat.last_seen_at.isoformat(),
                }
                for heartbeat in heartbeats
            ],
        }


async def _telegram_summary() -> dict[str, Any]:
    async with get_session_factory()() as session:
        messages = (
            await session.execute(
                select(ChannelMessage)
                .where(ChannelMessage.provider == "telegram")
                .order_by(ChannelMessage.created_at.desc())
                .limit(12)
            )
        ).scalars()
        return {
            "messages": [
                {
                    "id": str(message.id),
                    "direction": message.direction,
                    "status": message.status,
                    "content_preview": message.content[:100],
                    "error_code": message.error_code,
                    "created_at": message.created_at.isoformat(),
                }
                for message in messages
            ]
        }


async def _telegram_account_summary() -> dict[str, Any]:
    async with get_session_factory()() as session:
        accounts = list(
            (
                await session.scalars(
                    select(ChannelAccount)
                    .where(
                        ChannelAccount.provider == "telegram",
                        ChannelAccount.active.is_(True),
                    )
                    .order_by(ChannelAccount.created_at)
                )
            ).all()
        )
        return {
            "accounts": [
                {
                    "user_id": str(account.user_id),
                    "workspace_id": str(account.workspace_id),
                    "display_name": account.display_name,
                    "verified_at": (
                        account.verified_at.isoformat() if account.verified_at else None
                    ),
                }
                for account in accounts
            ]
        }


async def _invitation_summary() -> dict[str, Any]:
    settings = get_settings()
    configured_admins = settings.configured_platform_admin_ids
    async with get_session_factory()() as session:
        missing_admin_users: list[str] = []
        for user_id in configured_admins:
            if await session.get(AppUser, user_id) is None:
                missing_admin_users.append(str(user_id))
                continue
            await is_platform_admin(session, user_id, settings)
        await session.commit()
        rpc_result = (
            await session.execute(
                text(
                    """
                    SELECT result_code
                    FROM public.accept_telegram_invite(
                        :token_hash, :chat_id, :telegram_user_id, CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "token_hash": "0" * 64,
                    "chat_id": "invitation-health-check",
                    "telegram_user_id": "invitation-health-check",
                    "metadata": "{}",
                },
            )
        ).scalar_one()
        return {
            "registration_mode": settings.registration_mode,
            "invitation_policy": settings.invitation_policy,
            "configured_admins": len(configured_admins),
            "active_admins": await session.scalar(
                select(func.count(PlatformAdmin.user_id)).where(
                    PlatformAdmin.status == "active"
                )
            ),
            "missing_admin_users": missing_admin_users,
            "app_users": await session.scalar(select(func.count(AppUser.id))),
            "user_identities": await session.scalar(select(func.count(UserIdentity.id))),
            "invites": await session.scalar(select(func.count(ChannelInvite.id))),
            "accept_rpc_probe": rpc_result,
        }


async def _invitation_rpc_canary() -> dict[str, Any]:
    settings = get_settings()
    admin_id = next(iter(settings.configured_platform_admin_ids), None)
    if admin_id is None:
        raise RuntimeError("Nenhum administrador está configurado")
    async with get_session_factory()() as session:
        workspace_id = await session.scalar(
            select(Workspace.id).where(Workspace.owner_user_id == admin_id)
        )
        if workspace_id is None:
            raise RuntimeError("O administrador configurado não possui workspace")
        token_hash = uuid.uuid4().hex + uuid.uuid4().hex
        telegram_id = f"invitation-rpc-canary-{uuid.uuid4()}"
        invite = ChannelInvite(
            created_by_user_id=admin_id,
            created_by_workspace_id=workspace_id,
            token_hash=token_hash,
            purpose="personal_account",
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(invite)
        await session.flush()

        async def accept(subject: str) -> str:
            return (
                await session.execute(
                    text(
                        """
                        SELECT result_code
                        FROM public.accept_telegram_invite(
                            :token_hash, :chat_id, :telegram_user_id, CAST(:metadata AS jsonb)
                        )
                        """
                    ),
                    {
                        "token_hash": token_hash,
                        "chat_id": subject,
                        "telegram_user_id": subject,
                        "metadata": '{"first_name":"Canary","language_code":"pt-BR"}',
                    },
                )
            ).scalar_one()

        try:
            created = await accept(telegram_id)
            replayed = await accept(telegram_id)
            rejected_reuse = await accept(f"{telegram_id}-other")
        finally:
            await session.rollback()
        if (created != "created"):
            raise RuntimeError(f"Aceite inicial inesperado: {created}")
        if replayed != "already_accepted_by_same_identity":
            raise RuntimeError(f"Replay inesperado: {replayed}")
        if rejected_reuse != "unavailable":
            raise RuntimeError(f"Reuso por outra identidade inesperado: {rejected_reuse}")
        return {
            "created": created,
            "same_identity_replay": replayed,
            "different_identity_reuse": rejected_reuse,
            "persisted": False,
        }


async def _enqueue_telegram_canary(text: str) -> dict[str, Any]:
    async with get_session_factory()() as session:
        account = await session.scalar(
            select(ChannelAccount)
            .where(
                ChannelAccount.provider == "telegram",
                ChannelAccount.active.is_(True),
            )
            .order_by(ChannelAccount.verified_at.desc(), ChannelAccount.created_at.desc())
            .limit(1)
        )
        if account is None:
            raise RuntimeError("Nenhuma conta Telegram ativa foi encontrada")
        canary_id = f"deploy-canary:{uuid.uuid4()}"
        message, replayed = await ConversationService().ingest_telegram_text(
            session,
            chat_id=account.external_account_id,
            user_id=account.external_account_id,
            external_message_id=canary_id,
            text=text,
            timestamp=str(int(datetime.now(UTC).timestamp())),
        )
        if message is None:
            raise RuntimeError("A conta Telegram ativa não aceitou o canário")
        return {
            "message_id": str(message.id),
            "external_message_id": canary_id,
            "replayed": replayed,
        }


def _api_smoke(client: NorthflankClient) -> dict[str, Any]:
    service = client.service(API_SERVICE_ID)
    public_port = next((port for port in service.get("ports", []) if port.get("public")), None)
    if not public_port or not public_port.get("dns"):
        raise RuntimeError("A API ainda não possui DNS público")
    base_url = f"https://{public_port['dns']}"
    checks: dict[str, Any] = {"base_url": base_url}
    with httpx.Client(base_url=base_url, timeout=30, follow_redirects=True) as smoke_client:
        for path in ("/health", "/ready"):
            response = smoke_client.get(path)
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text[:500]
            checks[path] = {"status_code": response.status_code, "body": body}
            response.raise_for_status()
    return checks


def provision(client: NorthflankClient) -> None:
    project = client.project()
    service_ids = {item.get("id") for item in project.get("services", [])}
    job_ids = {item.get("id") for item in project.get("jobs", [])}
    # PUT gives us one stable secret group and safely refreshes values/revision on reruns.
    client.put_secret_group()
    if API_SERVICE_ID not in service_ids:
        client.create_api()
    project = client.project()
    service_ids = {item.get("id") for item in project.get("services", [])}
    if WORKER_SERVICE_ID not in service_ids:
        client.create_worker()
    if MIGRATION_JOB_ID not in job_ids:
        client.create_migration_job()
    print(json.dumps(_resource_summary(client.project()), indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Provisiona o backend no Northflank Sandbox")
    parser.add_argument(
        "command",
        choices=[
            "inspect",
            "provision",
            "build-api",
            "build-worker",
            "migrate",
            "migration-status",
            "smoke-api",
            "database-health",
            "telegram-health",
            "telegram-accounts",
            "invitation-health",
            "invitation-rpc-canary",
            "telegram-canary",
            "scheduler-canary",
        ],
    )
    args = parser.parse_args()
    client = NorthflankClient()
    if args.command == "inspect":
        print(json.dumps(_deployment_summary(client), indent=2, ensure_ascii=False))
    elif args.command == "provision":
        provision(client)
    elif args.command == "build-api":
        client.start_bundle_build(API_SERVICE_ID)
    elif args.command == "build-worker":
        client.start_bundle_build(WORKER_SERVICE_ID)
    elif args.command == "migrate":
        print(json.dumps(client.run_migrations(), indent=2, ensure_ascii=False))
    elif args.command == "migration-status":
        print(json.dumps(client.job_runs(MIGRATION_JOB_ID), indent=2, ensure_ascii=False))
    elif args.command == "smoke-api":
        print(json.dumps(_api_smoke(client), indent=2, ensure_ascii=False))
    elif args.command == "database-health":
        print(json.dumps(asyncio.run(_database_summary()), indent=2, ensure_ascii=False))
    elif args.command == "telegram-health":
        print(json.dumps(asyncio.run(_telegram_summary()), indent=2, ensure_ascii=False))
    elif args.command == "telegram-accounts":
        print(json.dumps(asyncio.run(_telegram_account_summary()), indent=2, ensure_ascii=False))
    elif args.command == "invitation-health":
        print(json.dumps(asyncio.run(_invitation_summary()), indent=2, ensure_ascii=False))
    elif args.command == "invitation-rpc-canary":
        print(json.dumps(asyncio.run(_invitation_rpc_canary()), indent=2, ensure_ascii=False))
    elif args.command == "telegram-canary":
        print(
            json.dumps(
                asyncio.run(_enqueue_telegram_canary("/help")),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.command == "scheduler-canary":
        print(
            json.dumps(
                asyncio.run(
                    _enqueue_telegram_canary(
                        "Daqui a 1 minuto, mande nesta conversa: "
                        "Canário do agendador remoto concluído."
                    )
                ),
                indent=2,
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc) or type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from exc
