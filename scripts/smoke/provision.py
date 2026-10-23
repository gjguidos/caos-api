
#!/usr/bin/env python3
"""Provision the disposable CAOS NCSC RabbitMQ smoke broker."""

import base64
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "http://rabbitmq:15672/api"

VHOST = "caos-smoke-validation"
EXCHANGE = "caos.smoke.validation"

AMQP_USER = "caos-smoke-validation-amqp"
MONITOR_USER = "caos-smoke-validation-monitor"

# Exchange and the two UUID-scoped test queues.
RESOURCE_PATTERN = (
    r"^(caos\.smoke\.validation|"
    r"caos\.smoke\.[0-9a-f]{32}\.(runtime|metadata))$"
)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


ADMIN_USER = require_env("SMOKE_ADMIN_USER")
ADMIN_PASSWORD = require_env("SMOKE_ADMIN_PASSWORD")


def request(method: str, path: str, payload: dict) -> None:
    url = f"{API}{path}"
    body = json.dumps(payload).encode("utf-8")

    credentials = base64.b64encode(
        f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode("utf-8")
    ).decode("ascii")

    req = Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urlopen(req, timeout=10) as response:
            if response.status not in (200, 201, 204):
                raise RuntimeError(
                    f"Provisioning failed: HTTP {response.status}"
                )
    except HTTPError as error:
        raise RuntimeError(
            f"RabbitMQ provisioning failed for {path}: HTTP {error.code}"
        ) from None


def put(path: str, payload: dict) -> None:
    request("PUT", path, payload)


def main() -> None:
    amqp_password = require_env("RABBITMQ_PASSWORD")
    monitor_password = require_env("CAOS_SMOKE_MANAGEMENT_PASSWORD")

    vhost = quote(VHOST, safe="")
    exchange = quote(EXCHANGE, safe="")

    print("Provisioning isolated NCSC RabbitMQ environment", flush=True)

    # Dedicated virtual host
    put(f"/vhosts/{vhost}", {"description": "Disposable CAOS NCSC smoke tests"})
    print("  Vhost configured", flush=True)

    # Dedicated durable topic exchange
    put(
        f"/exchanges/{vhost}/{exchange}",
        {
            "type": "topic",
            "durable": True,
            "auto_delete": False,
            "internal": False,
            "arguments": {},
        },
    )
    print("  Topic exchange configured", flush=True)

    # Restricted AMQP account
    put(
        f"/users/{quote(AMQP_USER, safe='')}",
        {"password": amqp_password, "tags": ""},
    )
    put(
        f"/permissions/{vhost}/{quote(AMQP_USER, safe='')}",
        {
            "configure": RESOURCE_PATTERN,
            "write": RESOURCE_PATTERN,
            "read": RESOURCE_PATTERN,
        },
    )
    print("  Restricted AMQP account configured", flush=True)

    # Read-only monitoring account
    put(
        f"/users/{quote(MONITOR_USER, safe='')}",
        {"password": monitor_password, "tags": "monitoring"},
    )
    put(
        f"/permissions/{vhost}/{quote(MONITOR_USER, safe='')}",
        {
            "configure": "^$",
            "write": "^$",
            "read": "^$",
        },
    )
    print("  Monitoring account configured", flush=True)

    print("PASS: NCSC RabbitMQ provisioning completed", flush=True)


if __name__ == "__main__":
    main()
