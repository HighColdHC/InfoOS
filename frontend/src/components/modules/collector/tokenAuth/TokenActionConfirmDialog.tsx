import type { TokenAdminRecord } from "../../../../lib/types/tokenAuth";
import { Modal } from "../../../ui/Modal";
import { Button } from "../../../ui/primitives/Button";

export type TokenAction = { kind: "revoke" | "rotate"; token: TokenAdminRecord };

type Props = {
  action: TokenAction | null;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => void;
};

export function TokenActionConfirmDialog({ action, busy, error, onClose, onConfirm }: Props) {
  if (!action) return null;
  const rotate = action.kind === "rotate";
  return (
    <Modal
      open
      title={rotate ? "轮换 Token" : "撤销 Token"}
      ariaLabel={rotate ? "轮换 Token" : "撤销 Token"}
      onClose={onClose}
      busy={busy}
      width={480}
      actions={
        <>
          <Button variant={rotate ? "primary" : "danger"} loading={busy} onClick={onConfirm}>
            {rotate ? "确认轮换" : "确认撤销"}
          </Button>
          <Button disabled={busy} onClick={onClose}>
            取消
          </Button>
        </>
      }
    >
      <div className="collector-token-action-copy">
        {error ? (
          <p className="collector-token-error" role="alert">
            {error}
          </p>
        ) : null}
        <strong>
          {action.token.name} · {action.token.token_prefix}
        </strong>
        <p>
          {rotate
            ? "旧 Token 会立即撤销，并生成一个只显示一次的新明文。历史请求日志会保留。"
            : "撤销后，新请求会立即返回 401；历史请求日志不会删除。"}
        </p>
      </div>
    </Modal>
  );
}
