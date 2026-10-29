import { useEffect, useState } from "react";
import { loadNotifications, saveNotifications, testNotifications } from "../../../../lib/api/notifications";
import type { NotificationSettings as Settings } from "../../../../lib/types/notifications";
import { FeishuConnect } from "./FeishuConnect";
import { Button } from "../../../ui/primitives/Button";

export function NotificationSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    void loadNotifications().then(value => { if (active) setSettings(value); })
      .catch(() => { if (active) setMessage("通知配置读取失败，请刷新页面重试。"); });
    return () => { active = false; };
  }, []);

  async function action(run: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try { await run(); }
    catch { setMessage("操作失败，请检查配置、应用权限和网络后重试。"); }
    finally { setBusy(false); }
  }

  if (!settings) return <section className="collector-notifications" aria-label="通知"><h2>通知</h2><p role="status">{message || "正在读取通知配置…"}</p></section>;
  const { config } = settings;
  const feishu = config.channels.feishu;
  function patchFeishu(patch: Partial<typeof feishu>) {
    setSettings({ ...settings!, config: { ...config, channels: { feishu: { ...feishu, ...patch } } } });
  }
  async function save() {
    const saved = await saveNotifications({ enabled: config.enabled,
      channels: { feishu: { enabled: feishu.enabled,
        ...(feishu.receive_id ? { receive_id: feishu.receive_id } : {}) } },
      content_push: config.content_push, quiet_hours: config.quiet_hours, auth_qr: config.auth_qr, agent_watchdog: config.agent_watchdog });
    setSettings(saved);
    return saved;
  }
  return <section className="collector-notifications" aria-label="通知">
    <div><h2>通知</h2><p>通过飞书自建应用私聊发送提醒与登录二维码。扫码会登录 InfoOS 专用浏览器，请勿转发。</p></div>
    <form onSubmit={event => { event.preventDefault(); void action(async () => { await save(); setMessage("通知配置已保存。"); }); }}>
      <fieldset disabled={busy}>
        <label className="collector-notifications-toggle"><input type="checkbox" checked={config.enabled} onChange={e => setSettings({ ...settings, config: { ...config, enabled: e.target.checked } })} />启用通知</label>
        <label className="collector-notifications-toggle"><input type="checkbox" checked={feishu.enabled} onChange={e => patchFeishu({ enabled: e.target.checked })} />启用飞书私聊</label>
        <label className="collector-notifications-toggle"><input type="checkbox" checked={config.content_push.daily_brief_digest} onChange={e => setSettings({ ...settings, config: { ...config, content_push: { daily_brief_digest: e.target.checked } } })} />推送日报摘要</label>
        <p>飞书：{settings.readiness.feishu ? "已连接" : "未连接"} · 接收人：{feishu.receive_id ? "已绑定" : "未绑定"}</p>
        <FeishuConnect onConnected={async () => { setSettings(await loadNotifications()); setMessage("飞书连接成功，测试通知已入队。"); }} />
        <label>静默开始<input type="time" required value={config.quiet_hours.start} onChange={e => setSettings({ ...settings, config: { ...config, quiet_hours: { ...config.quiet_hours, start: e.target.value } } })} /></label>
        <label>静默结束<input type="time" required value={config.quiet_hours.end} onChange={e => setSettings({ ...settings, config: { ...config, quiet_hours: { ...config.quiet_hours, end: e.target.value } } })} /></label>
        <label>时区<input required value={config.quiet_hours.timezone} onChange={e => setSettings({ ...settings, config: { ...config, quiet_hours: { ...config.quiet_hours, timezone: e.target.value } } })} /></label>
        <div className="collector-notifications-actions">
          <Button type="submit" loading={busy}>保存通知设置</Button>
          <Button type="button" onClick={() => void action(async () => {
            await save(); const result = await testNotifications();
            setMessage(result.results.length ? result.results.map(r => `${r.channel}：${r.status}${r.error_code ? `（${r.error_code}）` : ""}`).join("；") : "没有已就绪的渠道。");
            setSettings(await loadNotifications());
          })}>发送测试</Button>
        </div>
      </fieldset>
    </form>
    {message ? <p role="status">{message}</p> : null}
    {settings.latest_results.map(result => <p key={result.channel}>最近投递 · {result.channel}：{result.status}{result.last_error_code ? `（${result.last_error_code}）` : ""} · {new Date(result.updated_at).toLocaleString("zh-CN")}</p>)}
  </section>;
}
