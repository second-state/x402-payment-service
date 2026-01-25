"""Paywall adapter script loader."""
from functools import lru_cache
from pathlib import Path

_SCRIPT_PATH = Path(__file__).parent / "static" / "paywall_adapter.js"

@lru_cache(maxsize=1)
def get_paywall_adapter_script() -> str:
    """Load the paywall adapter JavaScript (cached)."""
    return _SCRIPT_PATH.read_text(encoding="utf-8")