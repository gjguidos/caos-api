import hashlib

from src.base.config.config import settings


def hash_jwt_payload(jwt_token: str) -> str:
    # Split the token into header, payload, and signature
    parts = jwt_token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT token format.")

    # Extract the payload part (which is the second part)
    payload_encoded = parts[1]
    # Add proper padding for base64 decoding
    padding = "=" * (-len(payload_encoded) % 4)
    payload_encoded += padding

    # Optionally, decode to JSON (if you need to inspect claims)
    # But for our purpose we can simply hash the encoded payload.
    # payload_bytes = base64.urlsafe_b64decode(payload_encoded)
    # payload = json.loads(payload_bytes)

    # Instead of using the whole token, hash just the payload segment
    session_key = hashlib.sha256(payload_encoded.encode("utf-8")).hexdigest()
    return session_key


def get_session_key(jwt_token: str, domain: str) -> str:
    application_key_prefix = settings.application.application_name
    token_hash = hash_jwt_payload(jwt_token)
    # Construct a namespaced key for the domain
    return f"{application_key_prefix}/{domain}:{token_hash}"
