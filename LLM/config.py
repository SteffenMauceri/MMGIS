import json
import os
from typing import Any, Callable, Dict, Optional


def _load_config() -> Dict[str, Any]:
    here = os.path.abspath(os.path.dirname(__file__))
    cfg_path = os.path.join(here, "config.json")
    if not os.path.isfile(cfg_path):
        return {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


_CONFIG: Dict[str, Any] = _load_config()


def _get_from_path(data: Dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def get_config_value(
    path: str,
    env_var: Optional[str],
    default: Any,
    cast: Optional[Callable[[Any], Any]] = None,
) -> Any:
    """Return value with precedence: ENV > config.json > default.

    - path: dot-separated key path inside config.json (e.g., "llm.base_url")
    - env_var: environment variable name that overrides if set
    - default: fallback when neither env nor config provide a value
    - cast: optional callable to coerce the value (e.g., int, float)
    """
    # 1) Environment variable override
    if env_var:
        val = os.getenv(env_var)
        if val is not None:
            try:
                return cast(val) if cast else val
            except Exception:
                # If cast fails, fall through to default
                pass

    # 2) Config file value
    cfg_val = _get_from_path(_CONFIG, path)
    if cfg_val is not None:
        try:
            return cast(cfg_val) if cast else cfg_val
        except Exception:
            pass

    # 3) Default
    return default


