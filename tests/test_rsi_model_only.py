from pathlib import Path

import pytest

from trace2task.rsi_model_only import TOOL_FEATURES, overrides, restricted_catalog


def test_catalog_removes_operational_tools_without_changing_original():
    original = {"models": [{"slug": "model", "context_window": 128000,
                            "shell_type": "unified_exec", "apply_patch_tool_type": "freeform",
                            "experimental_supported_tools": ["test_sync_tool"]}]}
    derived = restricted_catalog(original)
    assert derived["models"][0]["shell_type"] == "disabled"
    assert derived["models"][0]["apply_patch_tool_type"] is None
    assert derived["models"][0]["experimental_supported_tools"] == []
    assert derived["models"][0]["context_window"] == 128000
    assert original["models"][0]["shell_type"] == "unified_exec"


def test_config_disables_configured_external_tools_without_leaking_keys():
    config = {"mcp_servers": {"some.server": {"token": "secret"}},
              "plugins": {"plugin@vendor": {"enabled": True}}}
    settings = overrides(config, Path("/rsi/catalog.json"), set(TOOL_FEATURES))
    assert 'mcp_servers."some.server".enabled=false' in settings
    assert 'plugins."plugin@vendor".enabled=false' in settings
    assert "secret" not in str(settings)
    assert "features.view_image=false" in settings


def test_missing_security_controls_rejected():
    with pytest.raises(ValueError, match="required"):
        overrides({}, Path("/rsi/catalog.json"), {"shell_tool"})
