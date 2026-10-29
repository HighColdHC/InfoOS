import { SendRecoveryQrButton } from "./SendRecoveryQrButton";
import { CheckCircle2, MonitorUp, ShieldCheck } from "lucide-react";
import type { AuthRecoveryIncident } from "../../../../lib/types/authRecovery";
import { Button } from "../../../ui/primitives/Button";

type Props = {
  incident: AuthRecoveryIncident;
  error: string | null;
  opening: boolean;
  verifying: boolean;
  onOpenConsole: () => void;
  onVerify: () => void;
};

const statusLabel: Record<AuthRecoveryIncident["status"], string> = {
  awaiting_user: "等待完成登录",
  verifying: "正在验证",
  resolved: "已恢复",
};

export function AuthenticationRecoveryPanel({ incident, error, opening, verifying, onOpenConsole, onVerify }: Props) {
  const resolved = incident.status === "resolved";
  return (
    <section className={`collector-auth-recovery is-${incident.status}`} aria-label="登录恢复">
      <div className="collector-auth-recovery__heading">
        <div>
          <p className="collector-auth-recovery__eyebrow">登录恢复</p>
          <h2>{incident.platform_label}</h2>
          <p>有 {incident.affected_task_count} 个任务等待恢复后的登录会话。</p>
        </div>
        <span className="collector-auth-recovery__status">{statusLabel[incident.status]}</span>
      </div>

      {resolved ? (
        <div className="collector-auth-recovery__success" role="status">
          <CheckCircle2 aria-hidden="true" size={18} /> 已恢复，已恢复 {incident.recovered_task_count} 个任务。
        </div>
      ) : (
        <>
          <ol className="collector-auth-recovery__steps">
            <li>
              <strong>打开容器浏览器</strong>
              <span>只会打开此恢复流程所需的受认证控制台。</span>
            </li>
            <li>
              <strong>完成平台登录</strong>
              <span>在容器内完成扫码、密码、MFA 或二次确认。</span>
            </li>
            <li>
              <strong>完成并验证</strong>
              <span>关闭控制台后由服务端验证会话是否可用。</span>
            </li>
          </ol>
          {error ? (
            <div className="collector-auth-recovery__error" role="alert">
              {error}
            </div>
          ) : null}
          {incident.last_error_code ? (
            <p className="collector-auth-recovery__detail">最近状态：{incident.last_error_code}</p>
          ) : null}
          <SendRecoveryQrButton incidentId={incident.incident_id} platform={incident.platform} />
          <div className="collector-auth-recovery__actions">
            <Button icon={MonitorUp} loading={opening} onClick={onOpenConsole}>
              打开容器浏览器
            </Button>
            <Button variant="primary" icon={ShieldCheck} loading={verifying} onClick={onVerify}>
              完成并验证
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
