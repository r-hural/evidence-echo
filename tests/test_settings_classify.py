"""Wave 4a: classify_batch - default 40, 0 = off, per topic, EV_ECHO_CLASSIFY_BATCH."""
from dataclasses import replace

import pytest

from evidence_echo.core import config, settings, topics
from evidence_echo.core.settings import ConfigError


def test_default_scope_and_values():
    assert settings.GLOBAL_DEFAULTS["classify_batch"] == 40 and "classify_batch" in settings.OVERRIDES
    assert settings.check_value("classify_batch", "0", "f") == 0
    assert settings.check_value("classify_batch", 500, "f") == 500
    for bad in ("-1", "501", "x", "2.5", True):
        with pytest.raises(ConfigError, match="classify_batch"):
            settings.check_value("classify_batch", bad, "f")


def test_precedence(workspace, monkeypatch):
    assert config.load().classify_batch == 40
    settings.save_global(workspace, {"classify_batch": 10})
    assert config.load().classify_batch == 10
    t = topics.load_topic(workspace / "topics", "seo")
    topics.save_topic(workspace / "topics", replace(t, classify_batch=3))
    assert config.load().classify_batch == 3
    monkeypatch.setenv("EV_ECHO_CLASSIFY_BATCH", "0")
    assert config.load().classify_batch == 0
