
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class MessagingConfig(BaseSettings):
    """RabbitMQ messaging configuration."""

    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = Field(default=5672, ge=1, le=65535)
    rabbitmq_user: str = "guest"
    rabbitmq_password: SecretStr = SecretStr("guest")
    rabbitmq_virtual_host: str = "/"

    rabbitmq_heartbeat: int = 60
    rabbitmq_connection_timeout: float = 10.0
    rabbitmq_reconnect_interval: float = 5.0

    rabbitmq_prefetch_count: int = 10
    rabbitmq_publisher_confirms: bool = True

    rabbitmq_event_exchange: str = "caos.events.v2"
    rabbitmq_monitoring_queue: str = "monitoring"
    rabbitmq_monitoring_enabled: bool = True

    @property
    def url(self) -> str:
        """Build an AMQP connection URL."""
        user = quote(self.rabbitmq_user, safe="")
        password = quote(
            self.rabbitmq_password.get_secret_value(),
            safe="",
        )
        virtual_host = quote(
            self.rabbitmq_virtual_host,
            safe="",
        )

        return (
            f"amqp://{user}:{password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}"
            f"/{virtual_host}"
        )
