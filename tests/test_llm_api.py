import pytest

from evidence_echo.core import llm_api


def test_missing_key_message_names_the_plugin_setting():
    with pytest.raises(ValueError) as exc:
        llm_api.AnthropicBackend("")
    message = str(exc.value)
    assert "plugin settings" in message
    assert "ANTHROPIC_API_KEY" not in message  # keys come only from the plugin settings
    assert "session" in message
