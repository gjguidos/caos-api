from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings

from src.base.config.mixins.agent_exposable_settings_mixin import AgentExposableSettingsMixin


class BrowserSessionSettings(AgentExposableSettingsMixin, BaseSettings):
    expose_to_agents: ClassVar[bool] = True

    callback_base_url: str = Field(
        default="http://host.docker.internal:8000",
        description="Base URL reachable from Browser Runner back to AgentVerse API.",
    )

    callback_path: str = Field(
        default="/api/v1/browser-sessions/callback",
        description="AgentVerse browser-session callback endpoint path.",
    )

    callback_token: str | None = Field(
        default=None,
        description=(
            "Shared token used by Browser Runner when calling AgentVerse "
            "browser-session callback endpoint."
        ),
    )

    api_base_url: str = Field(
        default="http://api:8000/api/v1/browser-sessions",
        description=(
            "Base URL used by internal AgentVerse services/tools when calling "
            "the Browser Session API."
        ),
    )

    render_page_path_template: str = Field(
        default="/{browser_session_id}/render",
        description="Browser Session render-page endpoint path template.",
    )

    api_timeout: int = Field(
        default=60,
        description="Timeout in seconds for Browser Session API calls.",
    )

    api_max_retries: int = Field(
        default=1,
        description="Max retries for Browser Session API calls.",
    )

    browser_runner_base_url: str = Field(
        default="http://host.docker.internal:7007",
        description="Base URL used by AgentVerse browser-session domain to call Browser Runner.",
    )

    browser_runner_start_session_path: str = Field(
        default="/sessions/start",
        description="Browser Runner endpoint path for starting an interactive session.",
    )

    browser_runner_status_path_template: str = Field(
        default="/sessions/{session_id}/status",
        description="Browser Runner endpoint path template for session status.",
    )

    browser_runner_storage_state_path_template: str = Field(
        default="/sessions/{session_id}/storage-state",
        description="Browser Runner endpoint path template for storage state.",
    )

    browser_runner_delete_session_path_template: str = Field(
        default="/sessions/{session_id}",
        description="Browser Runner endpoint path template for deleting a session.",
    )

    browser_runner_render_page_path_template: str = Field(
        default="/sessions/{session_id}/render",
        description="Browser Runner endpoint path template for rendering with an existing session.",
    )

    browser_runner_ephemeral_render_path: str = Field(
        default="/render",
        description="Browser Runner endpoint path for unauthenticated ephemeral browser rendering.",
    )

    browser_runner_timeout_seconds: float = Field(
        default=30.0,
        description="Timeout in seconds for Browser Runner API calls.",
    )

    @property
    def callback_url(self) -> str:
        return (
            f"{self.callback_base_url.rstrip('/')}"
            f"/{self.callback_path.lstrip('/')}"
        )


    @property
    def render_page_url_template(self) -> str:
        return (
            f"{self.api_base_url.rstrip('/')}/"
            f"{self.render_page_path_template.lstrip('/')}"
        )


    def public_dict(self) -> dict[str, Any]:
        return {
            "browser_session_callback_url": self.callback_url,
            "browser_session_render_page_url_template": self.render_page_url_template,
        }


    def render_page_url(self, browser_session_id: str) -> str:
        if not browser_session_id:
            raise ValueError("browser_session_id is required")

        return self.render_page_url_template.format(
            browser_session_id=browser_session_id
        )
    
    def browser_runner_start_session_url(self) -> str:
        return (
            f"{self.browser_runner_base_url.rstrip('/')}/"
            f"{self.browser_runner_start_session_path.lstrip('/')}"
        )


    def browser_runner_status_path(self, session_id: str) -> str:
        return self.browser_runner_status_path_template.format(
            session_id=session_id
        )


    def browser_runner_storage_state_path(self, session_id: str) -> str:
        return self.browser_runner_storage_state_path_template.format(
            session_id=session_id
        )


    def browser_runner_delete_session_path(self, session_id: str) -> str:
        return self.browser_runner_delete_session_path_template.format(
            session_id=session_id
        )


    def browser_runner_render_page_path(self, session_id: str) -> str:
        return self.browser_runner_render_page_path_template.format(
            session_id=session_id
        )