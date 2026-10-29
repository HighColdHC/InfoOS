"""Regression coverage for passive workbench API reads."""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.infrastructure.storage import bootstrap, database
from backend.infrastructure.storage.base import InfoOSRepository


pytestmark = pytest.mark.no_storage_bootstrap


PASSIVE_GETS = ("/api/configurable-sources",)


@pytest.fixture
def passive_client(tmp_path, monkeypatch):
    # App construction is the one configuring boundary allowed by this test.
    client = TestClient(create_app(base_dir=tmp_path))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("passive GET used a configuring storage seam")

    monkeypatch.setattr(InfoOSRepository, "connect", forbidden)
    monkeypatch.setattr(bootstrap, "initialize_runtime_storage", forbidden)

    real_sqlite_connect = sqlite3.connect

    def existing_only(database_name, *args, **kwargs):
        value = str(database_name)
        if not (kwargs.get("uri") is True and ("mode=rw" in value or "mode=ro" in value)):
            raise AssertionError(f"passive GET used direct/configuring sqlite connect: {value}")
        return real_sqlite_connect(database_name, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", existing_only)
    return client


@pytest.mark.parametrize("path", PASSIVE_GETS)
def test_passive_workbench_gets_do_not_configure_storage(passive_client, path):
    response = passive_client.get(path)
    assert response.status_code == 200, (path, response.text)
