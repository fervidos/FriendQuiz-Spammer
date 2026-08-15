from config import DEFAULTS, normalize_config, validate_config
from server import app


def test_healthz_endpoint():
    client = app.test_client()
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_normalize_config_keeps_defaults_and_casts_values():
    cfg = {
        "count": "25",
        "threads": "10",
        "delay_ms": "123",
        "letters": "false",
        "digits": "1",
        "min_score": "2",
        "max_score": "8",
    }

    normalized = normalize_config(cfg)

    assert normalized["count"] == 25
    assert normalized["threads"] == 10
    assert normalized["delay_ms"] == 123
    assert normalized["letters"] is False
    assert normalized["digits"] is True
    assert normalized["min_score"] == 2
    assert normalized["max_score"] == 8
    assert normalized["code"] == DEFAULTS["code"]


def test_validate_config_rejects_invalid_score_ranges():
    bad = {
        **DEFAULTS,
        "score_mode": "range",
        "min_score": 9,
        "max_score": 2,
    }

    try:
        validate_config(bad)
        assert False, "Expected ValueError for invalid score range"
    except ValueError:
        pass


def test_validate_config_sanitizes_invalid_counts_and_threads():
    bad = {**DEFAULTS, "count": -1, "threads": 0}

    cleaned = validate_config(bad)

    assert cleaned["count"] == 0
    assert cleaned["threads"] == 1
