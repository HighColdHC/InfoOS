import type { SourceManagementItem } from "../../../../lib/types/sourceManagement";
import { Modal } from "../../../ui/Modal";
import { Button } from "../../../ui/primitives/Button";

type Props = { source: SourceManagementItem | null; busy: boolean; onClose: () => void; onConfirm: () => void };

export function SourceActivationDialog({ source, busy, onClose, onConfirm }: Props) {
  if (!source) return null;
  const enabling = !source.enabled;
  const verb = enabling ? "启用" : "停用";
  return (
    <Modal open title={`${verb}信息源`} ariaLabel={`${verb}信息源`} onClose={onClose} busy={busy} width={460}
      actions={<><Button variant="primary" loading={busy} onClick={onConfirm}>{`确认${verb}`}</Button><Button disabled={busy} onClick={onClose}>取消</Button></>}>
      <p className="collector-source-activation-copy">确定{verb}“{source.display_name}”？历史运行和信息卡将完整保留。</p>
    </Modal>
  );
}
