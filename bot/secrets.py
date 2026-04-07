"""
secrets.py — Load Kalshi credentials from GCP Secret Manager at runtime.

Secrets required in project hashbranch-clankers:
  kalshi-api-key-id    — the API key UUID
  kalshi-private-key   — RSA private key PEM contents

Falls back to env vars KALSHI_API_KEY / KALSHI_PRIVATE_KEY_DATA
for local dev (set KALSHI_PRIVATE_KEY_DATA to PEM string directly).
"""

import os
import tempfile
import subprocess
import logging

logger = logging.getLogger("forge.secrets")

PROJECT = "hashbranch-clankers"
_cache: dict = {}


def _read_secret(name: str) -> str:
    if name in _cache:
        return _cache[name]
    result = subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest",
         f"--secret={name}", f"--project={PROJECT}"],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to read secret '{name}': {result.stderr.strip()}")
    val = result.stdout.strip()
    _cache[name] = val
    return val


def get_api_key() -> str:
    env = os.environ.get("KALSHI_API_KEY", "")
    if env:
        return env
    return _read_secret("kalshi-api-key-id")


def get_private_key_path() -> str:
    """
    Returns a path to a temp file containing the PEM.
    Uses env var KALSHI_PRIVATE_KEY (path) if set.
    Otherwise pulls from Secret Manager and writes to a temp file.
    """
    env_path = os.environ.get("KALSHI_PRIVATE_KEY", "")
    if env_path and os.path.exists(env_path):
        return env_path

    pem = os.environ.get("KALSHI_PRIVATE_KEY_DATA", "")
    if not pem:
        pem = _read_secret("kalshi-private-key")

    # Write to a temp file (readable only by current user)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".pem", delete=False, prefix="kalshi_key_"
    )
    tmp.write(pem)
    tmp.flush()
    os.chmod(tmp.name, 0o600)
    logger.debug(f"Private key written to temp file: {tmp.name}")
    return tmp.name
