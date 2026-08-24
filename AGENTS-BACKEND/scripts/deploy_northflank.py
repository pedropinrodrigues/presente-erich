from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

PROJECT_ID = "presente-erich"
API_SERVICE_ID = "agents-api-prod"
WORKER_SERVICE_ID = "agents-worker-prod"
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

    def create_secret_group(self) -> None:
        payload = {
            "name": "Production Runtime",
            "description": "Agents backend production runtime variables",
            "type": "secret",
            "secretType": "environment",
            "priority": 10,
            "restrictions": {"restricted": False},
            "secrets": {"variables": _runtime_variables()},
        }
        self.request("POST", f"/projects/{PROJECT_ID}/secrets", payload)

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


def provision(client: NorthflankClient) -> None:
    project = client.project()
    service_ids = {item.get("id") for item in project.get("services", [])}
    if not service_ids:
        client.create_secret_group()
    if API_SERVICE_ID not in service_ids:
        client.create_api()
    project = client.project()
    service_ids = {item.get("id") for item in project.get("services", [])}
    if WORKER_SERVICE_ID not in service_ids:
        client.create_worker()
    print(json.dumps(_resource_summary(client.project()), indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Provisiona o backend no Northflank Sandbox")
    parser.add_argument("command", choices=["inspect", "provision", "build-api", "build-worker"])
    args = parser.parse_args()
    client = NorthflankClient()
    if args.command == "inspect":
        print(json.dumps(_resource_summary(client.project()), indent=2, ensure_ascii=False))
    elif args.command == "provision":
        provision(client)
    elif args.command == "build-api":
        client.start_bundle_build(API_SERVICE_ID)
    elif args.command == "build-worker":
        client.start_bundle_build(WORKER_SERVICE_ID)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
