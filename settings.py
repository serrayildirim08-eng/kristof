"""Load settings.yaml / sources.yaml and .env into plain dicts."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def load_yaml(name: str) -> dict:
    with open(ROOT / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> dict:
    s = load_yaml("settings.yaml")
    s["db_path"] = os.getenv("LEGAL_MONITOR_DB", str(ROOT / "data" / "legal_monitor.sqlite3"))
    s["root"] = str(ROOT)
    return s


def load_sources() -> list[dict]:
    return [src for src in load_yaml("sources.yaml").get("sources", []) if src.get("enabled", True)]
