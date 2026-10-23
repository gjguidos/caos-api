import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / ".smoke" / "credentials.env"


def main() -> None:
    DESTINATION.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    if DESTINATION.exists():
        print("Smoke credentials already exist; preserving them")
        return

    values = {
        "SMOKE_ADMIN_USER": "caos-smoke-admin",
        "SMOKE_ADMIN_PASSWORD": secrets.token_hex(32),
        "RABBITMQ_USER": "caos-smoke-validation-amqp",
        "RABBITMQ_PASSWORD": secrets.token_hex(32),
        "CAOS_SMOKE_MANAGEMENT_USER": "caos-smoke-validation-monitor",
        "CAOS_SMOKE_MANAGEMENT_PASSWORD": secrets.token_hex(32),
        "COGNITIVE_EVENT_SIGNING_KEY": secrets.token_hex(32),
    }

    content = "".join(f"{key}={value}\n" for key, value in values.items())

    with open(
        DESTINATION,
        "x",
        encoding="utf-8",
        opener=lambda path, flags: os.open(path, flags, 0o600),
    ) as file:
        file.write(content)

    print("Smoke credentials generated securely")


if __name__ == "__main__":
    main()