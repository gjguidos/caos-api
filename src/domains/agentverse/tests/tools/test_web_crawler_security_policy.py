from __future__ import annotations

import os
import sys
import types
from importlib import import_module
from pathlib import Path

import pytest

os.environ.setdefault(
    "AZURE_OPENAI_API_KEY", "test-key"
)
os.environ.setdefault(
    "AZURE_OPENAI_ENDPOINT",
    "https://example.openai.azure.com",
)

_DOMAINS_ROOT = Path(__file__).resolve().parents[3]
_WEB_CRAWLER_ROOT = Path(__file__).resolve().parents[2] / "tools" / "web_crawler_tool"

domains_pkg = types.ModuleType("src.domains")
domains_pkg.__path__ = [str(_DOMAINS_ROOT)]
sys.modules.setdefault("src.domains", domains_pkg)

web_crawler_pkg = types.ModuleType("src.domains.agentverse.tools.web_crawler_tool")
web_crawler_pkg.__path__ = [str(_WEB_CRAWLER_ROOT)]
sys.modules.setdefault("src.domains.agentverse.tools.web_crawler_tool", web_crawler_pkg)

security_pkg = types.ModuleType("src.domains.agentverse.tools.web_crawler_tool.security")
security_pkg.__path__ = [str(_WEB_CRAWLER_ROOT / "security")]
sys.modules.setdefault("src.domains.agentverse.tools.web_crawler_tool.security", security_pkg)

WebCrawlerSecurityConfig = import_module(
    "src.domains.agentverse.tools.web_crawler_tool.config"
).WebCrawlerSecurityConfig
_policy = import_module("src.domains.agentverse.tools.web_crawler_tool.security.policy")
evaluate_ip_security = _policy.evaluate_ip_security
evaluate_url_security = _policy.evaluate_url_security


def test_evaluate_url_security_blocks_localhost() -> None:
    verdict = evaluate_url_security(
        "https://localhost/health",
        security=WebCrawlerSecurityConfig(),
    )

    assert verdict.decision == "block"
    assert verdict.rule_id == "host.internal_blocked"


def test_evaluate_ip_security_blocks_private_cidr() -> None:
    verdict = evaluate_ip_security(
        "10.1.2.3",
        security=WebCrawlerSecurityConfig(),
    )

    assert verdict.decision == "block"
    assert verdict.rule_id == "ip.private_or_special"


def test_evaluate_url_security_blocks_host_not_in_allowlist() -> None:
    verdict = evaluate_url_security(
        "https://blocked.example.org/path",
        security=WebCrawlerSecurityConfig(allowed_domains=["example.com"]),
    )

    assert verdict.decision == "block"
    assert verdict.rule_id == "domain.not_allowed"


def test_evaluate_url_security_allows_host_in_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_policy, "resolve_host_ips", lambda _host: {"93.184.216.34"})

    verdict = evaluate_url_security(
        "https://docs.example.com/path",
        security=WebCrawlerSecurityConfig(allowed_domains=["example.com"]),
    )

    assert verdict.decision == "allow"
    assert verdict.rule_id == "url.allowed"


def test_evaluate_url_security_blocks_configured_blocked_domain() -> None:
    verdict = evaluate_url_security(
        "https://api.blocked.com/docs",
        security=WebCrawlerSecurityConfig(blocked_domains=["blocked.com"]),
    )

    assert verdict.decision == "block"
    assert verdict.rule_id == "domain.blocked"


def test_evaluate_url_security_blocks_inappropriate_keyword_classification() -> None:
    verdict = evaluate_url_security(
        "https://example.com/porn/content",
        security=WebCrawlerSecurityConfig(),
    )

    assert verdict.decision == "block"
    assert verdict.rule_id == "content.inappropriate_url"


def test_evaluate_url_security_honors_blocked_content_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_policy, "resolve_host_ips", lambda _host: {"93.184.216.34"})

    verdict = evaluate_url_security(
        "https://example.com/porn/content",
        security=WebCrawlerSecurityConfig(blocked_content_categories=["adult"]),
    )

    assert verdict.decision == "block"
    assert verdict.category == "adult"


def test_evaluate_url_security_allows_unblocked_content_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_policy, "resolve_host_ips", lambda _host: {"93.184.216.34"})

    verdict = evaluate_url_security(
        "https://example.com/porn/content",
        security=WebCrawlerSecurityConfig(blocked_content_categories=["gambling"]),
    )

    assert verdict.decision == "allow"
    assert verdict.rule_id == "url.allowed"
