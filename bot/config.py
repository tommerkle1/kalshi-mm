"""
config.py — Load and validate bot configuration from config.toml.
"""

try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def load() -> dict:
    cfg_path = ROOT / "config.toml"
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    return cfg


def get(key: str, default=None):
    cfg = load()
    keys = key.split(".")
    val = cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val
