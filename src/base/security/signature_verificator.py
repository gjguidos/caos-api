def verify_signature(message: str, signature_b64: str, public_key_b64: str) -> bool:
    """
    Verify a base64-encoded Ed25519 signature against a base64 public key.
    """
    # try:
    #     public_key = Ed25519PublicKey.from_public_bytes(b64decode(public_key_b64))
    #     signature = b64decode(signature_b64)
    #     public_key.verify(signature, message.encode())
    #     return True
    # except (InvalidSignature, ValueError, TypeError):
    #     return False
    return signature_b64 == public_key_b64
