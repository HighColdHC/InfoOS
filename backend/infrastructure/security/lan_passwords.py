"""Locked, atomic appliance htpasswd storage; passwords never enter argv."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hmac
import os
from pathlib import Path
import re
import subprocess
import tempfile


USERNAME = re.compile(r"[A-Za-z0-9@._+\-]{3,128}\Z")


def password_path() -> Path:
    return Path(os.environ.get("INFOOS_WEBAUTH_PASSWORD_FILE", "/config/webauth-htpasswd"))


def hash_password(password: str) -> str:
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    raw = password.encode("utf-8")
    if any(c in password for c in "\x00\r\n"):
        return False
    try:
        if encoded.startswith(("$2a$", "$2b$", "$2y$")):
            import bcrypt

            # Existing bcrypt databases may have accepted passwords longer than 72 bytes.
            return bcrypt.checkpw(raw[:72], encoded.encode("ascii"))
        if re.fullmatch(r"\$apr1\$[./A-Za-z0-9]{1,8}\$[./A-Za-z0-9]{22}", encoded):
            result = subprocess.run(
                ["openssl", "passwd", "-apr1", "-salt", encoded.split("$")[2], "-stdin"],
                input=raw + b"\n", capture_output=True, timeout=5, check=False,
            )
            return result.returncode == 0 and hmac.compare_digest(result.stdout.strip(), encoded.encode())
    except (ValueError, UnicodeError):
        return False
    return False


def atomic_write(path: Path, content: str) -> None:
    """Replace in the same directory, preserving owner and enforcing private mode."""
    metadata = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            if metadata and (metadata.st_uid, metadata.st_gid) != (os.getuid(), os.getgid()):
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class PasswordStore:
    def __init__(self, path: Path | None = None):
        self.path = path or password_path()

    @contextmanager
    def locked(self):
        # Separate stable inode: locking the replaced password file would race.
        fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield self
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        users = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            name, separator, encoded = line.partition(":")
            if not separator or not name or not encoded or name in users:
                raise ValueError("Invalid password database")
            users[name] = encoded
        return users

    def write(self, users: dict[str, str]) -> None:
        atomic_write(self.path, "".join(f"{name}:{encoded}\n" for name, encoded in users.items()))
