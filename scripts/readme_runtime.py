"""Allowlisted runtime facts for disposable README instances; no credentials."""
from __future__ import annotations

import json
from pathlib import Path

# Executed read-only inside the appliance. Never export the original state files,
# context IDs, PIDs, paths, account identities, errors, cookies or configuration.
REMOTE_FACTS = r'''
import importlib.metadata,json,os,pathlib,sqlite3,subprocess
from backend.infrastructure.browser import session_state
from backend.infrastructure.runtime.environment import setup_state_path
from backend.infrastructure.runtime.setup_state import load_state,COMPONENT_IDS
from backend.infrastructure.runtime.container_health import read_container_health
from backend.read_models.source_management import read_source_page
state = session_state.read()
setup = load_state(setup_state_path())
raw_session_path = session_state.JSON_PATH
shape = {}
if raw_session_path.exists():
    raw = json.loads(raw_session_path.read_text())
    shape = {k:type(raw[k]).__name__ for k in session_state.BrowserSessionState.__dataclass_fields__ if k in raw}

def version(command):
    result = subprocess.run(command,capture_output=True,text=True,timeout=10,check=True)
    return (result.stdout or result.stderr).strip().splitlines()[0][:160]

def package(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None

versions = {
    "dedicated_chrome": version([os.environ.get("INFOOS_CHROME_BIN","google-chrome-stable"),"--version"]).removeprefix("Google Chrome "),
    "yt_dlp": package("yt-dlp"), "gallery_dl": package("gallery-dl"),
    "ffmpeg": version(["ffmpeg","-version"]).split()[2],
    "local_asr": package("sherpa-onnx"), "sqlite": sqlite3.sqlite_version,
}
health_time = read_container_health()["checked_at"]
components = {}
for key in COMPONENT_IDS:
    saved = setup.components[key]
    if key == "opencli_bridge":
        # Installation metadata cannot prove connectivity. Preserve only the
        # explicitly checked, persisted product result and its original time.
        components[key] = {"status":saved.status, "version":saved.version,
                           "last_checked":saved.last_checked,"detail_code":saved.detail_code}
        continue
    found = versions.get(key) or saved.version
    components[key] = {"status": "installed" if found else saved.status, "version":found,
                       "last_checked":saved.last_checked or health_time,"detail_code":None if found else saved.detail_code}
health = read_container_health()
print(json.dumps({
    "session_shape":shape,
    "session":None if state is None else {"schema_version":state.schema_version,
        "is_alive":session_state.is_alive(state),"last_health_status":state.last_health_status,
        "binding_proof":state.binding_proof,"started_at":state.started_at,"last_health_at":state.last_health_at},
    "accounts":{k:{"status":v.status.value,"last_checked":v.last_checked} for k,v in setup.accounts.items()},
    "components":components,"component_snapshot_at":health_time,
    "runtime_health":health,
    "source_summary":read_source_page(pathlib.Path("."),limit=1)["summary"],
},ensure_ascii=False))
'''


def install_runtime_facts(home: Path, facts: dict) -> None:
    """Construct local-only snapshots using public shape and allowlisted facts."""
    state_dir = home / "state"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "setup-state.json").write_text(json.dumps({
        "current_step": "skipped", "accounts": facts["accounts"],
        "components": facts["components"], "component_snapshot_at": facts["component_snapshot_at"],
    }))
    session = facts["session"]
    if session and session["is_alive"]:
        (state_dir / "browser-session.json").write_text(json.dumps({
            "schema_version":session["schema_version"],
            "profile_dir":str(home / "unused-chrome-profile"),
            "context_id":"readme-demo-context", "alias":"infoos-fetch",
            "started_at":session["started_at"],"last_health_at":session["last_health_at"],
            "last_health_status":session["last_health_status"],
            "chrome_pid":1,"binding_proof":session["binding_proof"],
        }))
    (home / "container-health.json").write_text(json.dumps(facts["runtime_health"]))
