from __future__ import annotations

import re
from typing import Literal

MAX_CRAWL_PAGES = 1_000_000

# =============================================================================
# Trigger vocabulary
# =============================================================================
# These words activate the WebCrawlerTool from natural tool commands.
#
# Example:
#   @crawl --url=https://example.com
#   @web_crawl --url=https://example.com --max_pages=3
#
# Keep these short and action-oriented. Avoid adding semantic analysis words like
# "research" or "analyze" here, because this tool should stay focused on
# fetching, inspecting, and reporting web evidence.
TRIGGER_WORDS_WEB_CRAWLER = [
    "crawl",
    "web_crawl",
    "crawler",
    "scrape",
    "web_scrape",
    "fetch_page",
]


# Regex used by the tool router to detect explicit cracan wler activation.
#
# (?<!\w) prevents matching inside longer words.
# The trigger words are escaped defensively before joining into the regex.
#
# Matches:
#   @crawl
#   @web_crawl
#
# Does not intend to match:
#   my@crawl
#   autocrawler without @
PATTERN_WEB_CRAWLER = re.compile(
    rf"(?<!\w)@({'|'.join(map(re.escape, TRIGGER_WORDS_WEB_CRAWLER))})\b",
    re.IGNORECASE,
)


# =============================================================================
# HTTP identity
# =============================================================================
# Default User-Agent for outbound crawler requests.
#
# This should identify the tool clearly and avoid pretending to be a normal
# consumer browser. Enterprise crawlers should be transparent and respectful.
#
# Individual deployments can override this through WebCrawlerToolConfig.user_agent.
DEFAULT_USER_AGENT = (
    "AgentVerseWebCrawler/1.0 "
    "(enterprise research crawler; respectful limits)"
)


# =============================================================================
# Baseline host/IP denylist
# =============================================================================
# These are high-risk hostnames that should never be crawled by default.
#
# IMPORTANT:
# This exact-host denylist is only one layer. Production safety should also use:
#   - blocked host suffixes, e.g. .internal, .svc.cluster.local
#   - DNS resolution checks
#   - private/link-local IP range blocking
#   - redirect validation
#   - tenant/domain allowlists where appropriate
#
# Exact host blocking helps catch obvious dangerous targets early.
BLOCKED_HOSTS = {
    # Local machine / loopback aliases
    "localhost",

    # Google Cloud metadata service hostname.
    # Metadata endpoints can expose credentials, service identity, or instance data.
    "metadata.google.internal",
}


# Exact IP denylist for especially dangerous addresses.
#
# IMPORTANT:
# This should be complemented by CIDR/range checks for:
#   127.0.0.0/8
#   10.0.0.0/8
#   172.16.0.0/12
#   192.168.0.0/16
#   169.254.0.0/16
#   fc00::/7
#   fe80::/10
#
# Exact IPs are useful, but not sufficient as the only SSRF defense.
BLOCKED_IPS = {
    # Unspecified / invalid target
    "0.0.0.0",

    # Loopback
    "127.0.0.1",

    # Common cloud metadata IP used by AWS, Azure, GCP-style environments.
    "169.254.169.254",
}


# =============================================================================
# Security audit vocabulary
# =============================================================================
# SecurityAuditDecision describes the outcome of a security or crawl-scope check.
#
# allow:
#   The URL/action passed policy and may proceed.
#
# block:
#   The URL/action was denied by security policy.
#   Example: private IP, cloud metadata, inappropriate category.
#
# skip:
#   The URL/action was intentionally not followed due to crawl policy.
#   Example: outside same-domain scope.
#
# warn:
#   The URL/action is allowed but noteworthy.
#   Example: suspicious but not blocked by current policy.
#
# error:
#   The crawler attempted an operation and failed.
#   Example: fetch exception, timeout, unsupported content type.
SecurityAuditDecision = Literal["allow", "block", "skip", "warn", "error"]


# SecurityAuditStage identifies where in the crawl lifecycle the decision happened.
#
# start_url_validation:
#   Initial validation of the user-provided URL before crawl begins.
#
# queue_validation:
#   Validation of a URL before it is fetched from the crawl queue.
#
# discovered_link_validation:
#   Validation of a link found on a fetched page before adding it to the queue.
#
# redirect_validation:
#   Validation of a redirect target before following it.
#
# fetch_response:
#   Decision/error based on the HTTP response or browser fetch result.
#
# content_policy:
#   Content-type, response-size, or category-based decision.
#
# same_domain_policy:
#   Crawl-scope decision, usually from same_domain_only=True.
SecurityAuditStage = Literal[
    "start_url_validation",
    "queue_validation",
    "discovered_link_validation",
    "redirect_validation",
    "fetch_response",
    "content_policy",
    "same_domain_policy",
]


# =============================================================================
# Crawl graph vocabulary
# =============================================================================
# Node status describes the lifecycle state of a URL in the crawl graph.
#
# queued:
#   URL has been discovered and accepted as eligible, but not fetched yet.
#
# visited:
#   URL was fetched and produced a CrawledPage.
#
# skipped:
#   URL was discovered but not fetched due to crawl policy.
#   Example: outside same-domain scope.
#
# blocked:
#   URL was denied by security policy.
#
# error:
#   URL fetch was attempted but failed.
CrawlGraphNodeStatus = Literal[
    "queued",
    "visited",
    "skipped",
    "blocked",
    "error",
]


# Edge status describes the relationship between a source page and a target link.
#
# discovered:
#   The link was found, passed validation, and the target was added to the queue.
#
# followed:
#   The edge led to a target page that was actually fetched.
#
# skipped:
#   The link was found but ignored due to crawl policy.
#
# blocked:
#   The link was found but blocked by security policy.
#
# duplicate:
#   The link was found but the target was already visited or already queued.
CrawlGraphEdgeStatus = Literal[
    "discovered",
    "followed",
    "skipped",
    "blocked",
    "duplicate",
]


# =============================================================================
# Form inventory vocabulary
# =============================================================================
# Input types that may collect sensitive or personally identifying information.
#
# These are not automatically "unsafe"; they are inventory signals used by the
# Form Inventory Report. Downstream workflows can decide how to interpret them.
SENSITIVE_INPUT_TYPES = {
    "password",
    "email",
    "tel",
    "file",
    "number",
    "date",
}


# Field-name/label keywords that suggest sensitive data collection.
#
# This is heuristic-based. It should be used as a signal, not a final compliance
# judgement. For example, "address" may be normal in checkout but relevant in a
# privacy/security review.
SENSITIVE_NAME_KEYWORDS = {
    "password",
    "passwd",
    "pwd",
    "email",
    "phone",
    "mobile",
    "ssn",
    "social",
    "personnummer",
    "credit",
    "card",
    "iban",
    "bank",
    "address",
    "birth",
    "dob",
    "dateofbirth",
}


# Keywords commonly associated with search forms.
#
# Used to classify forms/controls as search-related in the Form Inventory Report.
SEARCH_KEYWORDS = {
    "search",
    "query",
    "q",
    "keyword",
}


# Keywords commonly associated with authentication/login forms.
#
# Used to classify forms as auth-related and to surface potential credential
# collection fields.
AUTH_KEYWORDS = {
    "login",
    "log in",
    "signin",
    "sign in",
    "password",
    "username",
    "account",
}


# Keywords commonly associated with newsletter or subscription capture forms.
NEWSLETTER_KEYWORDS = {
    "newsletter",
    "subscribe",
    "subscription",
}


# Keywords commonly associated with contact, support, or feedback forms.
CONTACT_KEYWORDS = {
    "contact",
    "message",
    "feedback",
    "support",
    "name",
    "email",
}

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".svg",
    ".avif",
    ".mp4",
    ".webm",
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".webm",
    ".mov",
    ".m4v",
    ".avi",
}

LOGO_HINTS = {
    "logo",
    "brand",
    "wordmark",
}

ICON_HINTS = {
    "icon",
    "sprite",
    "favicon",
}

HERO_HINTS = {
    "hero",
    "banner",
    "masthead",
    "cover",
    "campaign",
}

PRODUCT_HINTS = {
    "product",
    "sku",
    "item",
    "catalog",
    "range",
    "collection",
}

DECORATIVE_HINTS = {
    "decorative",
    "presentation",
    "spacer",
}

DOCUMENT_EXTENSIONS = {
    ".pdf": "pdf",
    ".doc": "word",
    ".docx": "word",
    ".odt": "word",
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".ods": "spreadsheet",
    ".csv": "spreadsheet",
    ".ppt": "presentation",
    ".pptx": "presentation",
    ".odp": "presentation",
    ".txt": "text",
    ".rtf": "text",
    ".md": "text",
    ".json": "data",
    ".xml": "data",
}

DOCUMENT_MIME_HINTS = {
    "application/pdf": "pdf",
    "application/msword": "word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "application/vnd.ms-excel": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
    "text/csv": "spreadsheet",
    "application/vnd.ms-powerpoint": "presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "presentation",
    "text/plain": "text",
    "application/json": "data",
    "application/xml": "data",
    "text/xml": "data",
}