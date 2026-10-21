
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version


@dataclass(frozen=True)
class VersionInfo:
    version: str
    revision: str
    channel: str


def git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
        return result.stdout.strip() or None
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return None


class VersionService:
    def __init__(self) -> None:
        self._version = self._resolve_version()

    def get_version(self) -> VersionInfo:
        return self._version

    def _resolve_version(self) -> VersionInfo:
        version = os.getenv("APP_VERSION")

        if not version:
            try:
                version = package_version("caos-api")
            except PackageNotFoundError:
                version = git_value(
                    "describe",
                    "--tags",
                    "--always",
                )

        revision = (
            os.getenv("APP_REVISION")
            or git_value("rev-parse", "--short", "HEAD")
            or "unknown"
        )

        channel = os.getenv("APP_CHANNEL")

        if not channel:
            branch = git_value("branch", "--show-current")
            channel = (
                "alpha" if branch == "alpha"
                else "stable" if branch == "master"
                else "development"
            )

        return VersionInfo(
            version=(version or "unknown").removeprefix("v"),
            revision=revision,
            channel=channel,
        )
