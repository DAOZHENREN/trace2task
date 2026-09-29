"""Launch a dedicated subscription App Server with host-operation tools removed.

Run this on the same server as the existing Codex login. Authentication is never
copied. Derived catalog/config files are private to the RSI deployment and do not
change the user's original model cache, config, plugins or credentials.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import tomllib
from pathlib import Path

TOOL_FEATURES = (
    "shell_tool", "unified_exec", "view_image", "apps", "browser_use",
    "browser_use_external", "in_app_browser", "multi_agent", "multi_agent_v2",
    "code_mode_host", "code_mode", "tool_suggest", "skill_mcp_dependency_install",
    "request_permissions_tool", "artifact", "image_generation",
)


def restricted_catalog(cache: dict) -> dict:
    models = cache.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("Codex model cache has no model catalog")
    derived = copy.deepcopy(models)
    for model in derived:
        if not isinstance(model, dict) or not isinstance(model.get("slug"), str):
            raise TypeError("Malformed Codex model metadata")
        model["shell_type"] = "disabled"
        model["apply_patch_tool_type"] = None
        model["experimental_supported_tools"] = []
    return {"models": derived}


def overrides(config: dict, catalog: Path, available_features: set[str]) -> list[str]:
    values = [
        f"model_catalog_json={json.dumps(str(catalog))}",
        'model_provider="openai"', 'web_search="disabled"',
        "project_doc_max_bytes=0", "skills.include_instructions=false",
        "include_environment_context=false",
    ]
    # Do not silently accept a binary without the essential feature switches.
    required = {"shell_tool", "view_image", "apps", "multi_agent", "code_mode_host"}
    if not required <= available_features:
        raise ValueError("Codex CLI lacks required model-only tool controls")
    values.extend(f"features.{name}=false" for name in TOOL_FEATURES
                  if name in available_features)
    for section in ("mcp_servers", "plugins"):
        configured = config.get(section, {})
        if not isinstance(configured, dict):
            raise TypeError(f"Malformed Codex {section} configuration")
        # Names may contain periods and @ signs: quote the whole TOML key.
        values.extend(f"{section}.{json.dumps(name)}.enabled=false" for name in configured)
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    version = subprocess.run([args.codex, "--version"], check=True, capture_output=True,
                             text=True, timeout=30).stdout.strip()
    if version != "codex-cli 0.156.1":
        raise RuntimeError("Model-only tool restrictions must be revalidated for this Codex version")
    source = args.codex_home.resolve()
    output = args.output.resolve()
    if output == source or source in output.parents:
        parser.error("Use an RSI deployment directory outside the user's Codex home")
    catalog_bytes = (source / "models_cache.json").read_bytes()
    catalog = restricted_catalog(json.loads(catalog_bytes))
    original_config = source / "config.toml"
    config = tomllib.loads(original_config.read_text(encoding="utf-8")) \
        if original_config.exists() else {}
    feature_result = subprocess.run([args.codex, "features", "list"], check=True,
                                    capture_output=True, text=True, timeout=30)
    features = {line.split()[0] for line in feature_result.stdout.splitlines() if line.strip()}
    output.mkdir(parents=True, exist_ok=True)
    # Content-addressed names avoid changing an in-flight server's catalog.
    data = json.dumps(catalog, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    catalog_path = output / f"model-only-{digest}.json"
    try:
        with catalog_path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if catalog_path.read_bytes() != data:
            raise RuntimeError("Existing model-only catalog is corrupt") from None
    settings = overrides(config, catalog_path, features)
    command = [args.codex, "app-server", "--strict-config"]
    for setting in settings:
        command.extend(["-c", setting])
    if args.check:
        print(json.dumps({"catalog_sha256": digest,
                          "source_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
                          "model_count": len(catalog["models"]),
                          "overrides": settings, "model_calls": 0}, indent=2))
        return
    os.environ["CODEX_HOME"] = str(source)
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
