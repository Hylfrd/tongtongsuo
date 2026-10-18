"""Local administrator password hashing; run with ``python -m app.admin_password``."""

import getpass
import hashlib
import hmac
import secrets

ITERATIONS = 600_000


def create_password_hash(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Administrator password must contain 12 to 256 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def parse_password_hash(encoded: str) -> tuple[bytes, bytes] | None:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or iterations != str(ITERATIONS):
            return None
        # Bound cost and reject whitespace / noncanonical or truncated encodings.
        if not 32 <= len(salt_hex) <= 128 or len(digest_hex) != 64:
            return None
        salt, digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if salt.hex() != salt_hex or digest.hex() != digest_hex:
            return None
        return salt, digest
    except (ValueError, TypeError):
        return None


def verify_password(password: str, parsed: tuple[bytes, bytes]) -> bool:
    salt, expected = parsed
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return hmac.compare_digest(actual, expected)


if __name__ == "__main__":
    password = getpass.getpass("Administrator password (12+ characters): ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    try:
        print(create_password_hash(password))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
