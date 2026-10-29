import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.infrastructure.connectors.opencli import OpenCLIConnector


def _completed(stdout: str, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_opencli_record_redacts_secret_like_keys(monkeypatch):
    def fake_run(command, **kwargs):
        return _completed(
            json.dumps(
                [
                    {
                        "id": "item-1",
                        "title": "Safe title",
                        "url": "https://example.com/1",
                        "token": "abc",
                        "nested": {
                            "authorization": "Bearer secret",
                            "safe": "kept",
                        },
                        "cookies": [{"session": "secret-session"}],
                    }
                ]
            )
        )

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_run)

    result = OpenCLIConnector(connector_id="opencli_safe", command=["demo", "list"]).fetch_new_items(None)

    record = result.items[0].raw_metadata["opencli_record"]
    assert record["token"] == "[REDACTED]"
    assert record["nested"]["authorization"] == "[REDACTED]"
    assert record["nested"]["safe"] == "kept"
    assert record["cookies"][0]["session"] == "[REDACTED]"
