"""Authenticated LAN credential changes, never a platform-login facility."""
from __future__ import annotations

from collections.abc import Callable
import json

from backend.infrastructure.observability.logging import get_logger
from backend.infrastructure.runtime.webauth_control import reload_and_invalidate_sessions
from backend.infrastructure.security.lan_account_limits import AccountFailureLimit
from backend.infrastructure.security.lan_passwords import PasswordStore, USERNAME, hash_password, verify_password


class AccountError(Exception):
    def __init__(self, status: int, message: str, reason: str):
        self.status, self.message, self.reason = status, message, reason
        super().__init__(message)


def audit(action: str, account: str | None, address: str, reason: str, new_name: str | None = None) -> None:
    # Explicit allowlist, never request bodies, exception strings, cookies or hashes.
    get_logger("lan_account.audit").info("lan_account %s", json.dumps({
        "action": action, "account": account, "address": address,
        "result": reason, "new_account": new_name,
    }, ensure_ascii=False))


def account_info() -> dict:
    store = PasswordStore()
    with store.locked():
        users = store.read()
    name = next(iter(users)) if len(users) == 1 else None
    return {"username": name, "requires_current_username": name is None,
            "legacy_hash": users[name].startswith("$apr1$") if name else None}


def change_account(action: str, payload: dict, address: str, authorize: Callable[[], bool]) -> dict:
    store = PasswordStore()
    account = new_name = None
    changed = False
    try:
        with store.locked():
            # Repeat after acquiring the lock so queued old sessions cannot mutate.
            if not authorize():
                raise AccountError(401, "请先登录后再操作", "unauthenticated")
            users = store.read()
            supplied = payload.get("current_username", "")
            account = next(iter(users)) if len(users) == 1 else supplied
            safe_account = account if USERNAME.fullmatch(account or "") else None
            account = safe_account
            limiter = AccountFailureLimit(store.path)
            if limiter.blocked(address, account or "unknown"):
                raise AccountError(429, "尝试次数过多，请在 15 分钟后重试", "rate_limited")
            current = payload.get("current_password", "")
            if not account or account not in users or not verify_password(current, users[account]):
                limiter.failed(address, account or "unknown")
                raise AccountError(400, "账户名或当前密码不正确", "invalid_credentials")
            if action == "password":
                password = payload.get("new_password", "")
                if password != payload.get("confirm_password"):
                    raise AccountError(400, "两次输入的新密码不一致", "confirmation_mismatch")
                if (len(password) < 12 or len(password.encode("utf-8")) > 72
                        or any(c in password for c in "\x00\r\n")
                        or account.casefold() in password.casefold()
                        or verify_password(password, users[account])):
                    raise AccountError(400, "新密码至少 12 位且不超过 72 字节，不能包含账户名、换行或与当前密码相同", "password_policy")
                users[account] = hash_password(password)
            else:
                new_name = payload.get("new_username", "")
                if not USERNAME.fullmatch(new_name) or new_name in users:
                    new_name = None
                    raise AccountError(400, "新账户名不可用；请使用 3–128 位字母、数字或 @._+-", "username_unavailable")
                users[new_name] = users.pop(account)
            store.write(users)
            changed = True
            reload_and_invalidate_sessions()
            audit(action, account, address, "success", new_name)
        return {"ok": True, "reauthenticate": True, "login_url": "/login/",
                "message": "修改成功，所有会话已失效，请重新登录"}
    except AccountError as exc:
        audit(action, account, address, exc.reason, new_name)
        raise
    except Exception:  # noqa: BLE001 - Credential file, bcrypt and reload subprocess failures must return an audited safe error with write status
        reason = "changed_restart_unconfirmed" if changed else "service_unavailable"
        audit(action, account, address, reason, new_name)
        message = "凭据已写入，但会话重启未确认，请联系管理员并使用新凭据重新登录" if changed else "账号服务暂不可用，请稍后重试"
        raise AccountError(503, message, reason) from None
