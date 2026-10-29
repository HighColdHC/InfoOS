import { useEffect, useState, type FormEvent } from "react";
import { changeLanAccount, fetchLanAccount, returnToLogin, type LanAccount } from "../../../lib/api/lanAccount";
import "./lanAccount.css";

export function LanAccountModule() {
  const [account, setAccount] = useState<LanAccount | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    fetchLanAccount().then(value => { if (active) setAccount(value); })
      .catch(() => { if (active) setError("无法读取账号信息，请确认已通过内网 HTTPS 登录"); });
    return () => { active = false; };
  }, []);
  return <section className="lan-account">
    <p>管理此设备的内网登录凭据。修改成功后，所有已登录设备都需要重新登录。</p>
    {error && <p role="alert">{error}</p>}
    {!account && !error && <p role="status">正在读取账号信息…</p>}
    {account && <>
      <p>当前账户：<strong>{account.username ?? "多个账户，请在表单中填写当前账户名"}</strong></p>
      {account.legacy_hash && <p className="lan-account__notice">建议修改一次密码以升级加密方式</p>}
      <div className="lan-account__forms">
        <AccountForm action="password" account={account} />
        <AccountForm action="username" account={account} />
      </div>
    </>}
  </section>;
}

function AccountForm({ action, account }: { action: "password" | "username"; account: LanAccount }) {
  const [currentName, setCurrentName] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newValue, setNewValue] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const isPassword = action === "password";
  const prefix = `account-${action}`;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    const name = account.username ?? currentName;
    if (account.requires_current_username && !/^[A-Za-z0-9@._+\-]{3,128}$/.test(name)) {
      setError("请填写有效的当前账户名"); return;
    }
    if (isPassword) {
      if (newValue !== confirm) { setError("两次输入的新密码不一致"); return; }
      if (Array.from(newValue).length < 12 || new TextEncoder().encode(newValue).length > 72
        || newValue === currentPassword || newValue.toLowerCase().includes(name.toLowerCase())) {
        setError("新密码至少 12 位且不超过 72 字节，不能包含账户名或与当前密码相同"); return;
      }
    } else if (!/^[A-Za-z0-9@._+\-]{3,128}$/.test(newValue) || newValue === name) {
      setError("请使用不同的账户名，限 3–128 位字母、数字或 @._+-"); return;
    }
    setPending(true);
    try {
      const result = await changeLanAccount(action, {
        current_username: name, current_password: currentPassword,
        ...(isPassword ? { new_password: newValue, confirm_password: confirm } : { new_username: newValue }),
      });
      setCurrentPassword(""); setNewValue(""); setConfirm("");
      setSuccess(result.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "修改失败，请稍后重试");
      setPending(false);
    }
  }
  useEffect(() => {
    if (!success) return;
    const timer = window.setTimeout(returnToLogin, 1800);
    return () => window.clearTimeout(timer);
  }, [success]);
  return <form onSubmit={submit} className="lan-account__form" aria-label={isPassword ? "修改密码" : "修改账户名"}>
    <h2>{isPassword ? "修改密码" : "修改账户名"}</h2>
    <p>{isPassword ? "至少 12 位，不包含账户名。最多 72 个 UTF-8 字节（中文通常占 3 字节）。" : "支持邮箱形式，例如 name@example.com。"}</p>
    <fieldset disabled={pending}>
      {account.requires_current_username && <label htmlFor={`${prefix}-current-name`}>当前账户名
        <input id={`${prefix}-current-name`} value={currentName} onChange={e => setCurrentName(e.target.value)} autoComplete="username" maxLength={128} required />
      </label>}
      <label htmlFor={`${prefix}-current`}>当前密码
        <input id={`${prefix}-current`} type="password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} autoComplete="current-password" required />
      </label>
      <label htmlFor={`${prefix}-new`}>{isPassword ? "新密码" : "新账户名"}
        <input id={`${prefix}-new`} type={isPassword ? "password" : "text"} value={newValue}
          onChange={e => setNewValue(e.target.value)} autoComplete={isPassword ? "new-password" : "username"} maxLength={128} required />
      </label>
      {isPassword && <label htmlFor={`${prefix}-confirm`}>确认新密码
        <input id={`${prefix}-confirm`} type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" required />
      </label>}
      <button className="lan-account__submit" type="submit">{pending ? "正在处理…" : isPassword ? "更新密码" : "更新账户名"}</button>
    </fieldset>
    {error && <p role="alert">{error}</p>}
    {success && <p role="status">{success}，即将跳转。<a href="/login/">立即登录</a></p>}
  </form>;
}
