"""Exercise the real shell initializer with local stand-ins for image binaries."""
import os
from pathlib import Path
import subprocess
import sys

from backend.infrastructure.security.lan_passwords import verify_password

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy/docker/rootfs/etc/cont-init.d/10-webauth.sh"


def test_init_only_empty_database(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    # macOS lacks the image's htpasswd/flock; keep bcrypt real and inspect argv.
    commands = {
        "flock": "import fcntl,sys; fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX)\n",
        "htpasswd": '''import sys
import bcrypt
from pathlib import Path
assert sys.argv[1:5] == ['-B', '-C', '12', '-i']
assert len(sys.argv) == 7
raw = sys.stdin.buffer.read().rstrip(b'\\n')
assert raw.decode() not in repr(sys.argv)
Path(sys.argv[5]).write_text(sys.argv[6] + ':' + bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode() + '\\n')
''',
    }
    for name, source in commands.items():
        path = binaries / name
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o755)
    database = tmp_path / "passwords"
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
               INFOOS_WEBAUTH_PASSWORD_FILE=str(database), WEB_AUTHENTICATION="1", SECURE_CONNECTION="1",
               USER_ID=str(os.getuid()), GROUP_ID=str(os.getgid()))
    env.pop("WEB_AUTHENTICATION_USERNAME", None)
    env.pop("WEB_AUTHENTICATION_PASSWORD", None)
    def run():
        return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True)
    missing = run()
    assert missing.returncode != 0 and "empty web authentication database" in missing.stderr
    database.write_text("\n# no users yet\n")
    env.update(WEB_AUTHENTICATION_USERNAME="test@example.com", WEB_AUTHENTICATION_PASSWORD="init-test-secret")
    initialized = run()
    assert initialized.returncode == 0, initialized.stderr
    name, encoded = database.read_text().strip().split(":")
    assert name == "test@example.com" and encoded.startswith("$2b$12$")
    assert verify_password("init-test-secret", encoded)
    assert database.stat().st_mode & 0o777 == 0o600
    before = database.read_bytes()
    env.update(WEB_AUTHENTICATION_USERNAME="different", WEB_AUTHENTICATION_PASSWORD="ignored-secret")
    preserved = run()
    assert preserved.returncode == 0 and "ignored" in preserved.stdout
    assert database.read_bytes() == before
    assert "ignored-secret" not in preserved.stdout + preserved.stderr
    assert "test@example.com" not in initialized.stdout + initialized.stderr
    env["SECURE_CONNECTION"] = "0"
    assert run().returncode != 0


def test_login_page_posts_locally_without_remote_assets():
    from html.parser import HTMLParser
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = []
        def handle_starttag(self, tag, attrs):
            self.elements.append((tag, dict(attrs)))
    login = ROOT / "deploy/docker/rootfs/opt/noVNC/login"
    parser = Elements()
    parser.feed((login / "index.html").read_text())
    forms = [attrs for tag, attrs in parser.elements if tag == "form"]
    assert forms[0]["method"] == "post" and forms[0]["action"] == "/login/login"
    inputs = {attrs.get("name"): attrs for tag, attrs in parser.elements if tag == "input"}
    assert inputs["password"]["type"] == "password"
    assert inputs["username"]["autocomplete"] == "username"
    for tag, attrs in parser.elements:
        path = attrs.get("src") if tag == "script" else attrs.get("href") if tag == "link" else None
        if path:
            assert "://" not in path and (login / path).is_file()
