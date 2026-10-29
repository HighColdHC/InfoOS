import type { SourceManagementDetail } from "../../../../lib/types/sourceManagement";
import { Modal } from "../../../ui/Modal";
import { Button } from "../../../ui/primitives/Button";
import { partialAcquisitionLabel } from "../../../../lib/acquisition";

type Props = {
  detail: SourceManagementDetail | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onEdit: () => void;
  onRun: () => void;
};

export function SourceDetailDialog({ detail, loading, error, onClose, onEdit, onRun }: Props) {
  if (!loading && !detail && !error) return null;
  const source = detail?.source;
  return (
    <Modal
      open
      title={source ? `信息源详情 · ${source.display_name}` : "信息源详情"}
      ariaLabel="信息源详情"
      onClose={onClose}
      width={760}
      actions={
        source ? (
          <>
            <Button variant="primary" onClick={onRun}>
              立即采集
            </Button>
            <Button onClick={onEdit}>编辑配置</Button>
            <Button variant="ghost" onClick={onClose}>
              关闭
            </Button>
          </>
        ) : (
          <Button onClick={onClose}>关闭</Button>
        )
      }
    >
      {loading ? (
        <div className="collector-source-detail__state" role="status">
          正在读取来源详情…
        </div>
      ) : null}
      {error ? (
        <div className="collector-source-detail__state is-error" role="alert">
          {error}
        </div>
      ) : null}
      {detail && source ? (
        <div className="collector-source-detail">
          <section>
            <h3>当前配置</h3>
            <dl className="collector-source-detail__facts">
              <Fact label="来源 ID" value={source.source_id} />
              <Fact label="稳定标识" value={source.identifier} />
              <Fact label="平台 / 类型" value={`${source.platform} · ${source.source_type}`} />
              <Fact label="采集频率" value={`每 ${source.cadence_hours} 小时`} />
              <Fact label="连接器" value={source.connector_id || "未记录"} />
              <Fact label="下次运行" value={formatDate(source.next_run_at)} />
              <Fact label="当前配置版本" value={detail.current_config_version} />
              <Fact label="生效时间" value={formatDate(detail.config_effective_at)} />
              <Fact
                label="已生成卡片"
                value={String(source.generated_card_count)}
              />
              <Fact label="健康状态" value={source.health_label} />
              <Fact label="最近运行时间" value={formatDate(detail.recent_run_stats.last_run_at)} />
              <Fact label="最近运行状态" value={partialAcquisitionLabel(detail.recent_run_stats) ?? runStatusLabel(detail.recent_run_stats.last_status)} />
              {detail.recent_run_stats.completeness_status === "partial" &&
                <Fact label="停止原因" value={detail.recent_run_stats.stop_reason ?? "未知"} />}
            </dl>
          </section>
          <section>
            <h3>健康与最近运行</h3>
            <p className="collector-source-detail__explanation">{detail.status_explanation}</p>
            <div className="collector-source-detail__stats">
              <div><span className="sr-only">近 7 天运行 {detail.recent_run_stats.total}</span><span aria-hidden="true">近 7 天运行</span><strong aria-hidden="true">{detail.recent_run_stats.total}</strong></div>
              <div><span className="sr-only">成功 {detail.recent_run_stats.success}</span><span aria-hidden="true">成功</span><strong aria-hidden="true">{detail.recent_run_stats.success}</strong></div>
              <div><span className="sr-only">失败 {detail.recent_run_stats.failed}</span><span aria-hidden="true">失败</span><strong aria-hidden="true">{detail.recent_run_stats.failed}</strong></div>
              <div><span className="sr-only">卡片 {detail.recent_run_stats.cards_created}</span><span aria-hidden="true">卡片</span><strong aria-hidden="true">{detail.recent_run_stats.cards_created}</strong></div>
            </div>
          </section>
        </div>
      ) : null}
    </Modal>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
function formatDate(value?: string | null) {
  if (!value) return "未计划";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function runStatusLabel(value?: string | null) {
  return (
    (
      { success: "成功", failed: "失败", blocked: "需处理", running: "运行中", pending: "排队中" } as Record<
        string,
        string | undefined
      >
    )[value ?? ""] ??
    value ??
    "未运行"
  );
}
