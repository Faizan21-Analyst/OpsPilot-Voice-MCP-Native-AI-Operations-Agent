#!/usr/bin/env python3
"""Build the Azure Container App definition (containerapp.yaml) used by the deploy workflow.

One Container App, four containers that share localhost (so the MCP servers stay private to the replica):

    gateway  (FastAPI + voice, the only container with ingress)   -> 127.0.0.1:8000
    docs     (policy search MCP server)                            -> 127.0.0.1:8001
    sql      (employee / ticket MCP server)                        -> 127.0.0.1:8002
    ops      (password reset / access MCP server)                  -> 127.0.0.1:8003

An init container creates and seeds the SQLite database once, before the others start (the same job compose did with
`depends_on`). The database lives on a temporary volume: it is recreated on every cold start.

Everything comes from environment variables, so no secret is ever written into the repository. Secret values are
never printed. Usage (the workflow does this for you):

    IMAGE_TAG=<git sha> ACR_LOGIN_SERVER=<acr>.azurecr.io ... python deploy/render_containerapp.py containerapp.yaml
"""
import os
import sys

import yaml

# ---- sizing -------------------------------------------------------------------------------------------------------
# Container Apps (consumption plan) allows at most 4 vCPU / 8 GiB per replica, and every container must use the
# 1 vCPU : 2 GiB ratio from the platform's size table. The init container is counted too, to stay safely under the cap.
SIZES = {            # name: (vCPU, GiB)
    "gateway": (1.75, 3.5),   # speech-to-text calls out; text-to-speech runs here and is CPU hungry
    "docs": (1.5, 3.0),       # embedding + reranker models
    "sql": (0.25, 0.5),
    "ops": (0.25, 0.5),
    "init": (0.25, 0.5),
}
MAX_CPU, MAX_GIB = 4.0, 8.0

# ---- settings -----------------------------------------------------------------------------------------------------
# name in the container app -> environment variable of this script that holds the value
SECRETS = {
    "groq-api-key": "GROQ_API_KEY",
    "gemini-api-key": "GEMINI_API_KEY",
    "jwt-secret": "AUTH_JWT_SECRET",
    "llama-parser": "LLAMA_PARSER",
    "qdrant-api-key": "QDRANT_API_KEY",
    "admin-password": "OPSPILOT_ADMIN_PASSWORD",
    "demo-password": "OPSPILOT_DEMO_PASSWORD",
}
# Extra Groq keys (other organisations = separate rate limits). Used only when the GitHub secret is set.
# container-app secret name -> (script variable, environment variable inside the containers)
OPTIONAL_SECRETS = {
    "groq-api-key-2": ("GROQ_API_KEY2", "GROQ__API_KEY2"),
    "groq-api-key-3": ("GROQ_API_KEY3", "GROQ__API_KEY3"),
    "groq-api-key-4": ("GROQ_API_KEY4", "GROQ__API_KEY4"),
}
# Optional plain settings, passed on only when the GitHub variable is set: environment variable inside the containers -> script variable
OPTIONAL_PLAIN = {
    "GROQ__FALLBACK_MODEL": "GROQ_FALLBACK_MODEL",
    "GEMINI__FALLBACK_MODEL": "GEMINI_FALLBACK_MODEL",
    "LITELLM__MAX_OUTPUT_TOKENS": "LLM_MAX_OUTPUT_TOKENS",
    "OPSPILOT_TTS_THREADS": "TTS_THREADS",
}
REQUIRED_PLAIN = ["IMAGE_TAG", "ACR_LOGIN_SERVER", "ENVIRONMENT_ID", "APP_IDENTITY_ID", "AZURE_LOCATION", "QDRANT_HTTP"]
DEFAULTS = {
    "GROQ_MODEL": "qwen/qwen3.8-27b",
    "GROQ_TIMEOUT_S": "15.0",
    "GEMINI_MODEL": "gemini/gemini-3.6-flash",
    "LITELLM_NUM_RETRIES": "2",
    "LITELLM_TIMEOUT_S": "20.0",
    "OPSPILOT_IDLE_TIMEOUT_S": "240",
    "OPSPILOT_SILENCE_TIMEOUT": "1.0",
    "OPSPILOT_STT_MODEL": "whisper-large-v3",
}
DB_URL = "sqlite+aiosqlite:////app/data/opspilot.db"
BUILT_IN_DEMO_PASSWORDS = {"admin123", "employee123"}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def get(name: str) -> str:
    # An unset GitHub variable arrives as an EMPTY string, so empty must fall back to the default too.
    return (os.environ.get(name) or DEFAULTS.get(name, "")).strip()


def check_inputs() -> None:
    missing = [n for n in REQUIRED_PLAIN if not get(n)] + [v for v in SECRETS.values() if not get(v)]
    if missing:
        fail("these GitHub secrets / variables are not set: " + ", ".join(missing))
    for name in ("OPSPILOT_ADMIN_PASSWORD", "OPSPILOT_DEMO_PASSWORD"):
        value = get(name)
        if value in BUILT_IN_DEMO_PASSWORDS or len(value) < 10 or len(value.encode()) > 72:
            fail(f"{name} must be 10 to 72 bytes long and not a built-in demo password")
    if len(get("AUTH_JWT_SECRET")) < 32:
        fail("AUTH_JWT_SECRET must be at least 32 characters (python -c \"import secrets; print(secrets.token_hex(32))\")")
    cpu = sum(c for c, _ in SIZES.values())
    mem = sum(m for _, m in SIZES.values())
    if cpu > MAX_CPU or mem > MAX_GIB:
        fail(f"container sizes add up to {cpu} vCPU / {mem} GiB, over the {MAX_CPU} vCPU / {MAX_GIB} GiB replica limit")


def secret_env(name: str, secret: str) -> dict:
    return {"name": name, "secretRef": secret}


def plain_env(name: str, value: str) -> dict:
    return {"name": name, "value": value}


def shared_env() -> list[dict]:
    """Settings every container needs (each service loads the full settings object at start-up)."""
    optional = [secret_env(env_name, secret) for secret, (var, env_name) in OPTIONAL_SECRETS.items() if get(var)]
    optional += [plain_env(env_name, get(var)) for env_name, var in OPTIONAL_PLAIN.items() if get(var)]
    return optional + [
        plain_env("ENV", "prod"),                      # production mode: the built-in demo passwords are refused
        plain_env("LOG_LEVEL", "INFO"),
        plain_env("LOGURU_LEVEL", "INFO"),             # Pipecat logs at DEBUG by default, which is a lot of log data
        plain_env("PYTHONUNBUFFERED", "1"),
        plain_env("GROQ__MODEL", get("GROQ_MODEL")),
        plain_env("GROQ__TIMEOUT_S", get("GROQ_TIMEOUT_S")),
        secret_env("GROQ__API_KEY", "groq-api-key"),
        plain_env("GEMINI__MODEL", get("GEMINI_MODEL")),
        secret_env("GEMINI__API_KEY", "gemini-api-key"),
        plain_env("LITELLM__NUM_RETRIES", get("LITELLM_NUM_RETRIES")),
        plain_env("LITELLM__TIMEOUT_S", get("LITELLM_TIMEOUT_S")),
        secret_env("AUTH__JWT_SECRET", "jwt-secret"),
        secret_env("LLAMA_PARSER", "llama-parser"),
        secret_env("QDRANT_API_KEY", "qdrant-api-key"),
        plain_env("QDRANT_HTTP", get("QDRANT_HTTP")),
        plain_env("DATABASE__URL", DB_URL),
        secret_env("OPSPILOT_ADMIN_PASSWORD", "admin-password"),
        secret_env("OPSPILOT_DEMO_PASSWORD", "demo-password"),
    ]


def container(name: str, image_suffix: str, extra_env: list[dict] | None = None, **extra) -> dict:
    cpu, mem = SIZES[name]
    spec = {
        "name": name,
        "image": f"{get('ACR_LOGIN_SERVER')}/opspilot-{image_suffix}:{get('IMAGE_TAG')}",
        "resources": {"cpu": cpu, "memory": f"{mem:g}Gi"},
        "env": shared_env() + (extra_env or []),
        "volumeMounts": [{"volumeName": "appdata", "mountPath": "/app/data"}],
    }
    spec.update(extra)
    return spec


def build() -> dict:
    gateway_probes = [
        # Start-up is allowed up to 20 x 30 s (10 min). /readyz only passes once the docs, sql and ops MCP servers accept
        # connections (the docs server loads its models first), so no traffic reaches the gateway before the full tool
        # list is available. This replaces compose's `depends_on: service_healthy`.
        {"type": "Startup", "httpGet": {"path": "/readyz", "port": 8000}, "initialDelaySeconds": 10,
         "periodSeconds": 30, "timeoutSeconds": 10, "failureThreshold": 20},
        # Readiness stays on the cheap /healthz: if one MCP server crashes later, the circuit breaker degrades that tool
        # only, instead of taking the whole site (chat, portal, login) out of rotation.
        {"type": "Readiness", "httpGet": {"path": "/healthz", "port": 8000}, "periodSeconds": 15,
         "timeoutSeconds": 10, "failureThreshold": 5},
        # Deliberately lenient: restarting the gateway would cut live voice sessions.
        {"type": "Liveness", "httpGet": {"path": "/healthz", "port": 8000}, "initialDelaySeconds": 30,
         "periodSeconds": 30, "timeoutSeconds": 10, "failureThreshold": 10},
    ]
    template = {
        "initContainers": [
            container("init", "ops", command=["python", "-m", "src.mcp_servers.sql.migrate"]),
        ],
        "containers": [
            container("gateway", "gateway", [
                plain_env("MCP_SERVERS__DOCS_URL", "http://127.0.0.1:8001/mcp"),
                plain_env("MCP_SERVERS__SQL_URL", "http://127.0.0.1:8002/mcp"),
                plain_env("MCP_SERVERS__OPS_URL", "http://127.0.0.1:8003/mcp"),
                plain_env("OPSPILOT_IDLE_TIMEOUT_S", get("OPSPILOT_IDLE_TIMEOUT_S")),
                plain_env("OPSPILOT_SILENCE_TIMEOUT", get("OPSPILOT_SILENCE_TIMEOUT")),
                plain_env("OPSPILOT_STT_MODEL", get("OPSPILOT_STT_MODEL")),
            ], probes=gateway_probes),
            # The embedding / reranker models are baked into this image, so never call out to Hugging Face.
            container("docs", "docs", [plain_env("HF_HUB_OFFLINE", "1")]),
            container("sql", "sql"),
            container("ops", "ops"),
        ],
        # Scale to zero when idle (nothing billed); never more than one replica (conversations live in memory).
        "scale": {
            "minReplicas": 0,
            "maxReplicas": 1,
            "rules": [{"name": "http", "http": {"metadata": {"concurrentRequests": "10"}}}],
        },
        "volumes": [{"name": "appdata", "storageType": "EmptyDir"}],
    }
    configuration = {
        "activeRevisionsMode": "Single",
        "maxInactiveRevisions": 2,
        "ingress": {
            "external": True,
            "targetPort": 8000,
            "transport": "auto",          # supports the voice WebSocket
            "allowInsecure": False,       # plain http is redirected to https
            "traffic": [{"weight": 100, "latestRevision": True}],
        },
        "registries": [{"server": get("ACR_LOGIN_SERVER"), "identity": get("APP_IDENTITY_ID")}],
        "secrets": [{"name": name, "value": get(var)} for name, var in SECRETS.items()]
        + [{"name": name, "value": get(var)} for name, (var, _) in OPTIONAL_SECRETS.items() if get(var)],
    }
    return {
        "location": get("AZURE_LOCATION"),
        "identity": {"type": "UserAssigned", "userAssignedIdentities": {get("APP_IDENTITY_ID"): {}}},
        "properties": {"environmentId": get("ENVIRONMENT_ID"), "configuration": configuration, "template": template},
    }


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: render_containerapp.py <output.yaml>")
    check_inputs()
    definition = build()
    # 0o600: the file holds the secret values and exists only on the (temporary) build machine.
    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        yaml.safe_dump(definition, out, sort_keys=False)
    cpu = sum(c for n, (c, _) in SIZES.items() if n != "init")
    mem = sum(m for n, (_, m) in SIZES.items() if n != "init")
    print(f"Rendered {sys.argv[1]}: image tag {get('IMAGE_TAG')[:12]}, app containers {cpu} vCPU / {mem:g} GiB, "
          f"scale 0..1, {len(SECRETS)} secrets (values not shown).")


if __name__ == "__main__":
    main()
