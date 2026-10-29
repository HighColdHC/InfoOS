import type { SourceManagementItem } from "../../../../lib/types/sourceManagement";
import { platformLabel } from "../../../../lib/platforms";
import { Modal } from "../../../ui/Modal";
import { Button } from "../../../ui/primitives/Button";

type Props = { source: SourceManagementItem | null; busy: boolean; onClose: () => void; onSubmit: () => void };

export function SourceRunConfirmDialog({ source, busy, onClose, onSubmit }: Props) {
  if (!source) return null;
  const blocked = source.readiness_status === "blocked" || source.readiness_status === "login_required";
  const platform = platformLabel(source.platform);
  if (blocked) {
    return (
      <Modal open title="采集环境需要处理" ariaLabel="采集环境需要处理" onClose={onClose} width={480} actions={<Button onClick={onClose}>关闭</Button>}>
        <div className="collector-source-run-prompt">
          <strong>{platform} 登录状态不可用</strong>
          <p>{source.status_explanation || "请先在专用 Chrome 中完成平台登录和状态检查。"}</p>
          <a className="btn btn--primary btn--md" href="?menu=environment">前往采集环境</a>
        </div>
      </Modal>
    );
  }
  return (
    <Modal
      open title="提交手动采集" ariaLabel="提交手动采集" onClose={onClose} width={480} busy={busy}
      actions={<><Button variant="primary" loading={busy} onClick={onSubmit}>提交采集任务</Button><Button disabled={busy} onClick={onClose}>取消</Button></>}
    >
      <div className="collector-source-run-prompt">
        <strong>{source.display_name}</strong>
        <p>将创建一个新的 Source Run 并交给持久化任务处理；页面不会同步执行抓取。</p>
      </div>
    </Modal>
  );
}
