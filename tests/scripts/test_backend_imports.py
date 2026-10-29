"""Every backend module must remain importable after structural refactors."""
from __future__ import annotations

import importlib
import pkgutil

def test_backend_modules_import_in_isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("INFOOS_HOME", str(tmp_path / "program"))
    monkeypatch.setenv("INFOOS_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("INFOOS_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("INFOOS_LLM_CONFIG", str(tmp_path / "state" / "llm.json"))

    backend = importlib.import_module("backend")
    for module in pkgutil.walk_packages(backend.__path__, prefix="backend."):
        if module.name == "backend.app.main":
            continue
        importlib.import_module(module.name)
