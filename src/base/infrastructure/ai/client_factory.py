from typing import Any


class ClientFactory:
    """
    Central locator for cognitive backends.
    - repos: {"llm": {"azure-gpt-4o": <repo>, "openai-gpt-4o": <repo>}, ...}
    - defaults: {"llm": "azure-gpt-4o", "image": "azure-gpt-image-1.5", ...}
    - noop_map: {"image": NoOpImageClient(), "video": NoOpVideoClient(), ...}
    """

    def __init__(
        self,
        repos: dict[str, dict[str, Any]],
        defaults: dict[str, str] | None = None,
        noop_map: dict[str, Any] | None = None,
    ):
        self.repos = repos or {}
        self.defaults = defaults or {}
        self.noop_map = noop_map or {}

    # --- basic lookups ------------------------------------------------
    def get_repo(self, domain: str, slug: str | None) -> Any | None:
        if not slug:
            return None
        return self.repos.get(domain, {}).get(slug)

    def get_default(self, domain: str) -> Any | None:
        default_key = self.defaults.get(domain) or next(iter(self.repos.get(domain, {})), "")
        return self.get_repo(domain, default_key)

    # --- fuzzy resolution ---------------------------------------------
    
    @staticmethod
    def _pick_key(
        group: dict[str, Any],
        preferred_key: str | None,
    ) -> str:
        if not group:
            return ""

        if not preferred_key:
            return next(iter(group), "")

        # Exact match
        if preferred_key in group:
            return preferred_key

        # Allow an unprefixed model name only if unambiguous.
        matches = [
            key
            for key in group
            if key.partition("-")[2] == preferred_key
        ]

        if len(matches) == 1:
            return matches[0]

        return ""


    def resolve_key(
        self,
        domain: str,
        preferred: str | None,
        required: bool = False,
    ) -> str:
        group = self.repos.get(domain, {})

        if preferred:
            key = self._pick_key(group, preferred)

            if not key and required:
                raise ValueError(
                    f"No available '{domain}' backend "
                    f"matches '{preferred}'"
                )

            return key

        configured_default = self.defaults.get(domain)

        if configured_default:
            key = self._pick_key(
                group,
                configured_default,
            )

            if key:
                return key

            if required:
                raise ValueError(
                    f"Default '{domain}' backend "
                    f"'{configured_default}' is unavailable"
                )

            return ""

        key = next(iter(group), "")

        if not key and required:
            raise ValueError(
                f"No default '{domain}' backend configured"
            )

        return key


    def resolve(self, domain: str, preferred: str | None, required: bool = False) -> Any | None:
        k = self.resolve_key(domain, preferred, required=required)
        return self.get_repo(domain, k) if k else None

    # --- NoOp-friendly helpers ---------------------------------------
    def get_or_noop(self, domain: str, preferred: str | None = None) -> Any:
        repo = self.resolve(domain, preferred, required=False)
        if repo is not None:
            return repo
        return self.noop_map.get(domain)  # can still be None if you want it that way
