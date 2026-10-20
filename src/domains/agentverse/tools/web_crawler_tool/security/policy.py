from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from src.domains.agentverse.tools.web_crawler_tool.config import WebCrawlerSecurityConfig
from src.domains.agentverse.tools.web_crawler_tool.security.classify import (
    classify_url_by_heuristics,
)
from src.domains.agentverse.tools.web_crawler_tool.security.constants import (
    BLOCKED_HOST_SUFFIXES,
    BLOCKED_HOSTS,
    BLOCKED_IP_NETWORKS,
    CLOUD_METADATA_IPS,
)
from src.domains.agentverse.tools.web_crawler_tool.security.verdict import SecurityVerdict

_INAPPROPRIATE_CATEGORY_ALIASES = {
    "adult",
    "pornography",
    "sexual_content",
    "gambling",
    "illegal_drugs",
    "weapons",
    "extremism",
    "malware",
    "phishing",
    "piracy",
    "hate",
    "self_harm",
    "violence_graphic",
}


class CrawlerSecurityError(ValueError):
    pass


def evaluate_url_security(
    url: str,
    *,
    security: WebCrawlerSecurityConfig,
    start_url: str | None = None,
) -> SecurityVerdict:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")

    if not host:
        return SecurityVerdict(
            decision="block",
            reason="URL host is missing.",
            category="invalid_url",
            rule_id="url.host_missing",
        )

    if start_url and security.block_cross_domain_redirects:
        start_host = (urlparse(start_url).hostname or "").lower().rstrip(".")
        if start_host and host != start_host:
            return SecurityVerdict(
                decision="block",
                reason=f"Cross-domain redirect blocked: {host} (start host: {start_host})",
                category="redirect_policy",
                rule_id="redirect.cross_domain_blocked",
                details={"start_url": start_url, "start_host": start_host, "host": host},
            )

    # Domain allowlist. Strongest production boundary.
    if security.allowed_domains:
        if not _host_matches_any_domain(host, security.allowed_domains):
            return SecurityVerdict(
                decision="block",
                reason=f"Host is not in allowed_domains: {host}",
                category="domain_policy",
                rule_id="domain.not_allowed",
                details={"host": host, "allowed_domains": security.allowed_domains},
            )

    # Explicit configured blocklist.
    if _host_matches_any_domain(host, security.blocked_domains):
        return SecurityVerdict(
            decision="block",
            reason=f"Host is explicitly blocked: {host}",
            category="domain_policy",
            rule_id="domain.blocked",
            details={"host": host},
        )

    # Internal hostnames.
    if (security.block_localhost and _is_localhost_host(host)) or (
        security.block_internal_hostnames
        and is_blocked_host(host)
        and (security.block_localhost or not _is_localhost_host(host))
    ):
        return SecurityVerdict(
            decision="block",
            reason=f"Internal or unsafe host blocked: {host}",
            category="internal_host",
            rule_id="host.internal_blocked",
            details={"host": host},
        )

    # URL keyword blocklist (cheap pre-filter).
    url_lower = url.lower()
    for keyword in security.blocked_url_keywords or []:
        kw = str(keyword).strip().lower()
        if kw and kw in url_lower:
            return SecurityVerdict(
                decision="block",
                reason=f"URL matched blocked_url_keywords: {kw}",
                category="url_keyword_policy",
                rule_id="url.keyword_blocked",
                details={"url": url, "host": host, "keyword": kw},
            )

    # Inappropriate sites.
    if security.block_inappropriate_sites:
        category, reason = classify_url_by_heuristics(url)
        blocked_categories = {item.strip().lower() for item in security.blocked_content_categories}
        should_block_category = category and (
            category in blocked_categories
            or (
                category == "inappropriate_site"
                and bool(blocked_categories & _INAPPROPRIATE_CATEGORY_ALIASES)
            )
        )
        if should_block_category:
            return SecurityVerdict(
                decision="block",
                reason=reason or "URL matched inappropriate-site policy.",
                category=category,
                rule_id="content.inappropriate_url",
                details={"url": url, "host": host},
            )

    # DNS/IP boundary.
    resolved_ips = resolve_host_ips(host)

    if not resolved_ips:
        decision = "block" if security.mode == "strict" else "warn"
        return SecurityVerdict(
            decision=decision,
            reason=f"Host did not resolve to any IP addresses: {host}",
            category="dns_resolution",
            rule_id="dns.no_records",
            details={"host": host},
        )

    for ip in resolved_ips:
        ip_verdict = evaluate_ip_security(ip, security=security)
        if not ip_verdict.allowed:
            return ip_verdict

    return SecurityVerdict(
        decision="allow",
        reason="URL passed crawler security policy.",
        category="allowed",
        rule_id="url.allowed",
        details={"host": host, "resolved_ips": sorted(resolved_ips)},
    )


def evaluate_ip_security(
    ip: str,
    *,
    security: WebCrawlerSecurityConfig,
) -> SecurityVerdict:
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        return SecurityVerdict(
            decision="block",
            reason=f"Invalid resolved IP: {ip}",
            category="invalid_ip",
            rule_id="ip.invalid",
            details={"ip": ip},
        )

    if security.block_cloud_metadata and ip in CLOUD_METADATA_IPS:
        return SecurityVerdict(
            decision="block",
            reason=f"Cloud metadata IP blocked: {ip}",
            category="cloud_metadata",
            rule_id="ip.cloud_metadata",
            details={"ip": ip},
        )

    if security.block_private_networks:
        for network in BLOCKED_IP_NETWORKS:
            if ip_obj in ipaddress.ip_network(network):
                return SecurityVerdict(
                    decision="block",
                    reason=f"Private/special IP range blocked: {ip}",
                    category="private_network",
                    rule_id="ip.private_or_special",
                    details={"ip": ip, "network": network},
                )

        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return SecurityVerdict(
                decision="block",
                reason=f"Unsafe IP blocked: {ip}",
                category="private_network",
                rule_id="ip.unsafe_builtin",
                details={"ip": ip},
            )

    return SecurityVerdict(
        decision="allow",
        reason="IP passed crawler security policy.",
        category="allowed",
        rule_id="ip.allowed",
        details={"ip": ip},
    )


def enforce_url_security(
    url: str,
    *,
    security: WebCrawlerSecurityConfig,
    start_url: str | None = None,
) -> SecurityVerdict:
    verdict = evaluate_url_security(
        url,
        security=security,
        start_url=start_url,
    )

    if not verdict.allowed:
        raise CrawlerSecurityError(verdict.reason)

    return verdict


def is_blocked_host(host: str) -> bool:
    normalized = host.strip().lower().rstrip(".")

    if normalized in BLOCKED_HOSTS:
        return True

    return any(normalized.endswith(suffix) for suffix in BLOCKED_HOST_SUFFIXES)


def _is_localhost_host(host: str) -> bool:
    normalized = host.strip().lower().rstrip(".")
    return normalized == "localhost" or normalized.endswith(".localhost")


def resolve_host_ips(host: str) -> set[str]:
    try:
        records = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return set()

    ips: set[str] = set()

    for record in records:
        sockaddr = record[4]
        if sockaddr:
            ips.add(sockaddr[0])

    return ips


def _host_matches_any_domain(host: str, domains: list[str]) -> bool:
    host = host.lower().rstrip(".")

    for domain in domains:
        domain = domain.lower().strip().rstrip(".")
        if not domain:
            continue

        if host == domain:
            return True

        if host.endswith("." + domain):
            return True

    return False
