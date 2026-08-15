import json
import os
from typing import Any, Dict

CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))

DEFAULTS: Dict[str, Any] = {
    "code": "",
    "count": 100,
    "threads": 8,
    "delay_ms": 0,
    "score_mode": "random",
    "exact_score": 10,
    "min_score": 0,
    "max_score": 10,
    "name_mode": "random",
    "prefix": "",
    "suffix": "",
    "min_len": 3,
    "max_len": 8,
    "letters": True,
    "digits": True,
    "names_list": "",
}

ENV_MAP = {
    "QUIZ_CODE": "code",
    "COUNT": "count",
    "THREADS": "threads",
    "DELAY_MS": "delay_ms",
    "SCORE_MODE": "score_mode",
    "EXACT_SCORE": "exact_score",
    "MIN_SCORE": "min_score",
    "MAX_SCORE": "max_score",
    "NAME_MODE": "name_mode",
    "NAME_PREFIX": "prefix",
    "NAME_SUFFIX": "suffix",
    "MIN_LEN": "min_len",
    "MAX_LEN": "max_len",
    "NAME_LETTERS": "letters",
    "NAME_DIGITS": "digits",
    "NAMES_LIST": "names_list",
}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_config(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    """Normalize incoming config data to the internal schema."""
    base = dict(DEFAULTS)
    if isinstance(raw, dict):
        base.update(raw)

    normalized = dict(base)
    normalized["code"] = str(normalized.get("code", "")).strip()
    normalized["count"] = max(0, _coerce_int(normalized.get("count", DEFAULTS["count"]), DEFAULTS["count"]))
    normalized["threads"] = max(1, _coerce_int(normalized.get("threads", DEFAULTS["threads"]), DEFAULTS["threads"]))
    normalized["delay_ms"] = max(0, _coerce_int(normalized.get("delay_ms", DEFAULTS["delay_ms"]), DEFAULTS["delay_ms"]))
    normalized["exact_score"] = _coerce_int(normalized.get("exact_score", DEFAULTS["exact_score"]), DEFAULTS["exact_score"])
    normalized["min_score"] = _coerce_int(normalized.get("min_score", DEFAULTS["min_score"]), DEFAULTS["min_score"])
    normalized["max_score"] = _coerce_int(normalized.get("max_score", DEFAULTS["max_score"]), DEFAULTS["max_score"])
    normalized["min_len"] = max(1, _coerce_int(normalized.get("min_len", DEFAULTS["min_len"]), DEFAULTS["min_len"]))
    normalized["max_len"] = max(normalized["min_len"], _coerce_int(normalized.get("max_len", DEFAULTS["max_len"]), DEFAULTS["max_len"]))
    normalized["letters"] = _coerce_bool(normalized.get("letters", DEFAULTS["letters"]))
    normalized["digits"] = _coerce_bool(normalized.get("digits", DEFAULTS["digits"]))
    normalized["name_mode"] = str(normalized.get("name_mode", DEFAULTS["name_mode"])).strip() or DEFAULTS["name_mode"]
    normalized["score_mode"] = str(normalized.get("score_mode", DEFAULTS["score_mode"])).strip() or DEFAULTS["score_mode"]
    normalized["prefix"] = str(normalized.get("prefix", DEFAULTS["prefix"]))
    normalized["suffix"] = str(normalized.get("suffix", DEFAULTS["suffix"]))
    normalized["names_list"] = str(normalized.get("names_list", DEFAULTS["names_list"]))
    return normalized


def validate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Validate config and raise ValueError for invalid values."""
    normalized = normalize_config(cfg)

    if normalized["threads"] <= 0:
        raise ValueError("threads must be greater than 0")
    if normalized["count"] < 0:
        raise ValueError("count cannot be negative")
    if normalized["delay_ms"] < 0:
        raise ValueError("delay_ms cannot be negative")

    score_mode = normalized["score_mode"]
    if score_mode not in {"random", "perfect", "exact", "range"}:
        raise ValueError("score_mode must be one of: random, perfect, exact, range")

    if score_mode == "exact":
        normalized["exact_score"] = max(0, min(len(normalized.get("question_count", [0])), int(normalized["exact_score"]))) if "question_count" in normalized else max(0, int(normalized["exact_score"]))
    if score_mode == "range":
        lo = normalized["min_score"]
        hi = normalized["max_score"]
        if hi < lo:
            raise ValueError("max_score must be greater than or equal to min_score")

    if not normalized["letters"] and not normalized["digits"]:
        raise ValueError("letters and digits cannot both be disabled")

    return normalized


def load_saved_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    try:
        if not os.path.exists(path):
            return dict(DEFAULTS)
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return dict(DEFAULTS)
        return validate_config(data)
    except (json.JSONDecodeError, OSError, ValueError):
        return dict(DEFAULTS)


def save_config(cfg: Dict[str, Any], path: str = CONFIG_PATH) -> None:
    cleaned = validate_config(cfg)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, indent=2, ensure_ascii=False)
