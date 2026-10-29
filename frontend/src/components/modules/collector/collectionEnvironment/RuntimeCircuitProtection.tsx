import { SendRecoveryQrButton } from "./SendRecoveryQrButton";
import { MonitorUp, ShieldCheck } from "lucide-react";
import type { AuthRecoverySummary, RuntimeCircuit } from "../../../../lib/types/collectionEnvironment";
import { Button } from "../../../ui/primitives/Button";

const safeReasons: Record<string, string> = {
  quota_exceeded: "AI 供应商额度不足",
  rate_limited: "AI 供应商暂时限流",
  server_error: "AI 供应商服务异常",
  network_error: "无法连接 AI 供应商",
  network_timeout: "采集请求超时",
  timeout: "AI 请求超时",
  login_required: "登录状态需要恢复",
  provider_unavailable: "服务暂时不可用",
  upstream_unavailable: "服务暂时不可用",
  configuration_invalid: "配置需要检查",
  configuration_missing: "配置需要补全",
};

const stateLabels: Record<RuntimeCircuit["state"], string> = {
  closed: "正常",
  open: "已熔断",
  half_open: "恢复探测中",
};

const actionLabels: Record<RuntimeCircuit["user_action"], string> = {
  none: "无需人工操作",
  login_required: "需要登录",
  config_required: "检查配置",
  restore_provider_quota: "补充额度后，在模型设置中测试连接以恢复任务",
};

function formatTimestamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "即将执行"
    : date.toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" });
}

type Props = {
  circuits: RuntimeCircuit[];
  recoveries: AuthRecoverySummary[];
  openingRecoveryId: string | null;
  verifyingRecoveryId: string | null;
  onOpenRecovery: (recovery: AuthRecoverySummary) => void;
  onVerifyRecovery: (recovery: AuthRecoverySummary) => void;
};

export function RuntimeCircuitProtection({
  circuits,
  recoveries,
  openingRecoveryId,
  verifyingRecoveryId,
  onOpenRecovery,
  onVerifyRecovery,
}: Props) {
  const activeCircuits = circuits.filter((circuit) => circuit.state !== "closed");
  if (!activeCircuits.length) return null;

  return (
    <section className="collector-environment-protection" aria-label="运行保护">
      <div className="collector-environment-section-title">
        <div>
          <h2>运行保护</h2>
          <p>只读展示当前保护状态；恢复探测由运行时自行处理。</p>
        </div>
        <span>{activeCircuits.length} 项</span>
      </div>
      <div className="collector-environment-protection-grid">
        {activeCircuits.map((circuit) => {
          const sourceId = circuit.source_id ?? (circuit.scope_kind === "platform" && circuit.scope_key.startsWith("rss:")
            ? circuit.scope_key.slice(4) : undefined);
          const recovery =
            circuit.scope_kind === "platform" && !sourceId
              ? recoveries.find((item) => item.platform === circuit.scope_key)
              : undefined;
          return (
            <article
              className={`collector-environment-circuit is-${circuit.state}`}
              key={`${circuit.scope_kind}:${circuit.scope_key}`}
            >
              <div className="collector-environment-circuit__head">
                <strong>
                  {sourceId
                    ? `RSS 订阅 · ${circuit.source_name ?? sourceId}${circuit.source_name && circuit.source_name !== sourceId ? `（${sourceId}）` : ""}`
                    : `${circuit.scope_kind === "platform" ? "平台" : "供应商"} · ${circuit.scope_key}`}
                </strong>
                <span>{stateLabels[circuit.state]}</span>
              </div>
              <p>{circuit.reason_code ? (safeReasons[circuit.reason_code] ?? "暂时不可用") : "暂时不可用"}</p>
              <div className="collector-environment-circuit__meta">
                <span>
                  {circuit.next_probe_at ? `下次探测：${formatTimestamp(circuit.next_probe_at)}` : "需人工恢复"}
                </span>
                <span>{actionLabels[circuit.user_action]}</span>
                {circuit.consecutive_failures > 0 ? <span>连续失败 {circuit.consecutive_failures} 次</span> : null}
              </div>
              {recovery ? (
                <div className="collector-environment-circuit__recovery">
                  <strong>待处理登录恢复</strong>
                  <span>影响 {recovery.affected_task_count} 个任务</span>
                  <SendRecoveryQrButton incidentId={recovery.incident_id} platform={recovery.platform} />
                  <div>
                    <Button
                      size="sm"
                      icon={MonitorUp}
                      loading={openingRecoveryId === recovery.incident_id}
                      onClick={() => onOpenRecovery(recovery)}
                    >
                      打开授权面板
                    </Button>
                    <Button
                      size="sm"
                      variant="primary"
                      icon={ShieldCheck}
                      loading={verifyingRecoveryId === recovery.incident_id}
                      onClick={() => onVerifyRecovery(recovery)}
                    >
                      完成并验证
                    </Button>
                  </div>
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
