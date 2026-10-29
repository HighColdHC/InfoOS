import { useEffect, useRef, useState } from "react";
import { cancelFeishuConnection, connectFeishu, loadFeishuConnection } from "../../../../lib/api/notifications";
import type { FeishuConnection } from "../../../../lib/types/notifications";
import { Button } from "../../../ui/primitives/Button";

type Props = { onConnected: () => Promise<void> };

export function FeishuConnect({ onConnected }: Props) {
  const [connection, setConnection] = useState<FeishuConnection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const completed = useRef(false);
  const revision = useRef(0);
  const connected = useRef(onConnected);
  useEffect(() => { connected.current = onConnected; }, [onConnected]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const requestedRevision = revision.current;
        const result = await loadFeishuConnection();
        if (!active) return;
        if (requestedRevision === revision.current) {
          setConnection(result);
          setError("");
          if (result.status === "completed" && !completed.current) {
            await connected.current();
            completed.current = true;
          }
        }
      } catch {
        if (active) setError("飞书连接状态读取失败，正在重试。");
      }
      if (active) timer = setTimeout(() => void poll(), 2000);
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, []);

  async function action(cancel: boolean) {
    revision.current += 1;
    setBusy(true);
    setError("");
    try {
      if (!cancel) completed.current = false;
      setConnection(await (cancel ? cancelFeishuConnection() : connectFeishu()));
    } catch { setError("飞书连接操作失败，请重试。"); }
    finally { setBusy(false); }
  }
  const waiting = connection !== null && connection.status === "waiting_scan";
  return <div>
    <Button type="button" loading={busy} disabled={waiting} onClick={() => void action(false)}>扫码连接飞书</Button>
    {waiting ? <div>
      <p>{connection.step === "create_app"
        ? "第 1 步：用飞书 App 扫码创建 InfoOS 通知应用"
        : "第 2 步：扫码登录授权，扫码即绑定你的飞书账号，请勿转发"}</p>
      {connection.qr_version > 0
        ? <img src={`/api/settings/notifications/feishu/connect/qr.png?v=${connection.qr_version}`} alt="飞书连接二维码" width={256} height={256} style={{ maxWidth: "100%", height: "auto" }} />
        : <p>正在准备二维码…</p>}
      <Button type="button" disabled={busy} onClick={() => void action(true)}>取消连接</Button>
    </div> : null}
    {connection !== null && connection.status === "failed" ? <p role="status">飞书连接失败（{connection.error_code}），请重新扫码。</p> : null}
    {error ? <p role="status">{error}</p> : null}
  </div>;
}
