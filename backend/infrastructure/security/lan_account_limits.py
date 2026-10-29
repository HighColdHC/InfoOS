"""Persistent failures shared by web workers, under the password store lock."""
import hashlib
import json
from pathlib import Path
import time

from backend.infrastructure.security.lan_passwords import atomic_write


class AccountFailureLimit:
    def __init__(self, password_file: Path):
        self.path = password_file.with_name(password_file.name + ".failures")
        self.now = time.time()
        entries = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.entries = {key: [t for t in times if t > self.now - 900] for key, times in entries.items()}
        self.entries = {key: times for key, times in self.entries.items() if times}

    def keys(self, address: str, account: str) -> tuple[str, str]:
        return tuple(hashlib.sha256(value.encode()).hexdigest() for value in (
            "ip:" + address, "account:" + address + "\x00" + account,
        ))

    def blocked(self, address: str, account: str) -> bool:
        return any(len(self.entries.get(key, [])) >= 5 for key in self.keys(address, account))

    def failed(self, address: str, account: str) -> None:
        for key in self.keys(address, account):
            self.entries.setdefault(key, []).append(self.now)
        atomic_write(self.path, json.dumps(self.entries))
