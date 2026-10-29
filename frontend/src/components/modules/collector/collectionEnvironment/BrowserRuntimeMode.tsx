import { useEffect, useState } from "react";
import { loadBrowserVisibility, saveBrowserVisibility } from "../../../../lib/api";

export function BrowserRuntimeMode() {
  const [headless, setHeadless] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void loadBrowserVisibility()
      .then((value) => { if (current) setHeadless(value.headless_during_fetch); })
      .catch(() => { if (current) setMessage("无法读取浏览器运行方式"); });
    return () => { current = false; };
  }, []);

  async function update(nextHeadless: boolean) {
    const previous = headless;
    setHeadless(nextHeadless);
    setSaving(true);
    setMessage(null);
    try {
      await saveBrowserVisibility(nextHeadless);
      setMessage("已保存；下次启动或修复采集会话时生效");
    } catch (error) {
      setHeadless(previous);
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="collector-environment-runtime" aria-label="采集浏览器运行方式">
      <div>
        <strong>采集浏览器运行方式</strong>
        <span>登录与账号修复始终使用可见窗口；正常采集建议后台运行。</span>
      </div>
      <label>
        <span>运行方式</span>
        <select
          aria-label="采集浏览器运行方式"
          disabled={headless === null || saving}
          value={headless === false ? "visible" : "headless"}
          onChange={(event) => void update(event.target.value === "headless")}
        >
          <option value="headless">后台运行（Headless）</option>
          <option value="visible">显示浏览器窗口</option>
        </select>
      </label>
      {message ? <small role={message.includes("失败") || message.includes("无法") ? "alert" : "status"}>{message}</small> : null}
    </section>
  );
}
