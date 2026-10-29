import { useState } from "react";
import { sendRecoveryQr } from "../../../../lib/api/notifications";
import { Button } from "../../../ui/primitives/Button";

export function SendRecoveryQrButton({ incidentId, platform }: { incidentId: string; platform: string }) {
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState("");
  if (!["xiaohongshu", "douyin", "zsxq"].includes(platform)) return null;
  async function send() {
    setSending(true);
    try {
      const result = await sendRecoveryQr(incidentId);
      setMessage(["capturing", "waiting", "closing"].includes(result.round.status) ? "二维码轮次已启动或正在进行，请查看飞书。" : "今日轮次已达上限，稍后再试。");
    } catch { setMessage("无法发送，请先保存并测试飞书通知配置。"); }
    finally { setSending(false); }
  }
  return <div><Button loading={sending} onClick={() => void send()}>发送二维码到飞书</Button>{message ? <p role="status">{message}</p> : null}</div>;
}
