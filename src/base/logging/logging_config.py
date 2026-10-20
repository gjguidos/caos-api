import json
import logging
import re
import traceback

from src.base.config.config import settings
from src.base.logging.splunk_handler_async import AsyncSplunkHECHandler
from src.base.middlewares.request_context import RequestContextFilter
from src.base.utils.env_utils import is_local_development


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for different log levels."""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
        "RESET": "\033[0m",  # Reset
    }

    EXTRA_SKIP_KEYS = {
        "args",
        "colored_levelname",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "filename_only",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
        "asctime",
        "domain",
        "domain_label",
    }

    def format(self, record):
        # Add color to level name
        levelname = record.levelname
        if levelname in self.COLORS:
            colored_levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
            record.colored_levelname = colored_levelname
        else:
            record.colored_levelname = f"{levelname}"

        # Extract just the filename without extension
        if record.filename:
            filename = record.filename.replace(".py", "")
            record.filename_only = filename if filename != "__main__" else "app"
        else:
            record.filename_only = "unknown"

        formatted = super().format(record)

        if settings.logger.log_show_extras:
            extras = {
                key: value
                for key, value in record.__dict__.items()
                if key not in self.EXTRA_SKIP_KEYS
            }
            if extras:
                formatted = f"{formatted} | {extras}"

        return formatted


class StructuredJsonFormatter(logging.Formatter):
    """JSON formatter for structured logging in deployed environments."""

    EXTRA_SKIP_KEYS = {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }

    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = "".join(traceback.format_exception(*record.exc_info))

        if record.stack_info:
            log_entry["stack_info"] = record.stack_info

        for key, value in record.__dict__.items():
            if key not in self.EXTRA_SKIP_KEYS:
                try:
                    json.dumps(value)
                    log_entry[key] = value
                except (TypeError, OverflowError):
                    log_entry[key] = str(value)

        return json.dumps(log_entry, default=str)


class HealthCheckFilter(logging.Filter):
    """Suppresses uvicorn access log entries for the /health endpoint."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, list | tuple) and len(args) >= 3:
            path = args[2]
            if isinstance(path, str) and path in ("/health", "/"):
                return False
        return True


class RedactQueryCredentialsFilter(logging.Filter):
    """Strips credential-bearing query parameters out of log records.

    Browsers cannot set headers on a websocket handshake, so clients pass the JWT as
    `?token=`. Uvicorn's protocol loggers log the raw path with its query string, which
    put whole access tokens - roles, email, oid and all - into stdout and Splunk. This
    runs on the handlers rather than a single logger so it also covers anything else
    that logs a URL.
    """

    SENSITIVE_PARAMS = (
        "token",
        "access_token",
        "id_token",
        "refresh_token",
        "code",
        "api_key",
        "apikey",
        "client_secret",
    )

    PATTERN = re.compile(
        rf"(?i)([?&](?:{'|'.join(SENSITIVE_PARAMS)})=)[^&\s\"']+",
    )

    REPLACEMENT = r"\1[REDACTED]"

    @classmethod
    def redact(cls, value: str) -> str:
        return cls.PATTERN.sub(cls.REPLACEMENT, value)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "=" in record.msg:
            record.msg = self.redact(record.msg)

        if isinstance(record.args, tuple):
            record.args = tuple(
                self.redact(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: self.redact(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }

        return True


class LoggingConfig:
    """Configuration class for application logging setup."""

    splunk_handler: AsyncSplunkHECHandler | None = None

    @staticmethod
    def configure_third_party_loggers() -> None:
        """Reduce high-volume HTTP client logs during crawling."""
        for logger_name in (
            "httpx",
            "httpcore",
        ):
            logging.getLogger(logger_name).setLevel(logging.WARNING)

    @staticmethod
    def setup_logging() -> None:
        """
        Configure application logging with correlation ID support and improved formatting.
        """
        log_level = getattr(logging, settings.logger.log_level.upper(), logging.INFO)
        logger = logging.getLogger()
        logger.setLevel(log_level)

        LoggingConfig.configure_third_party_loggers()

        logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

        # Only add handlers if none exist to avoid duplicates
        if not logger.hasHandlers():
            # Add correlation filter to include correlation ID in logs
            filters = [RequestContextFilter(), RedactQueryCredentialsFilter()]

            LoggingConfig.add_console_logging(logger, filters)

            # Add Splunk logging if not local development
            if not is_local_development():
                LoggingConfig.add_splunk_logging(logger, filters)

    @staticmethod
    def add_console_logging(logger: logging.Logger, filters: list[logging.Filter]) -> None:
        handler = logging.StreamHandler()

        if is_local_development():
            format_string = "%(asctime)s | %(colored_levelname)s |%(filename_only)s | %(message)s"
            formatter = ColoredFormatter(format_string, datefmt="%Y-%m-%d %H:%M:%S")
        else:
            formatter = StructuredJsonFormatter(datefmt="%Y-%m-%dT%H:%M:%S")

        handler.setFormatter(formatter)

        for filter in filters:
            handler.addFilter(filter)

        logger.addHandler(handler)

    # @staticmethod
    # def add_splunk_logging(logger: logging.Logger, filters: list[logging.Filter]) -> None:
    #     splunk_token = settings.logger.splunk_hec_token or ""
    #     host = settings.logger.splunk_host or ""
    #     url = f"{host}/services/collector/event"
    #     application_name = settings.application.application_name

    #     if not (splunk_token and host):
    #         logger.warning(
    #             "Splunk logging is enabled but SPLUNK_HEC_TOKEN or SPLUNK_HOST is not set. Skipping Splunk logging setup."
    #         )
    #         return

    #     LoggingConfig.splunk_handler = AsyncSplunkHECHandler(
    #         host=host,
    #         token=splunk_token,
    #         url=url,
    #         application_name=application_name,
    #     )
    #     for filter in filters:
    #         LoggingConfig.splunk_handler.addFilter(filter)

    #     logger.addHandler(LoggingConfig.splunk_handler)
