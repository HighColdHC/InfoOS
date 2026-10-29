import { useEffect, useState } from "react";
import { Copy } from "lucide-react";
import type { TokenCreateResult } from "../../../../lib/types/tokenAuth";
import { Modal } from "../../../ui/Modal";
import { Button } from "../../../ui/primitives/Button";

type Props = { result: TokenCreateResult | null; onCopy: (value: string, label: string) => void; onClear: () => void };

export function TokenCreatedDialog({ result, onCopy, onClear }: Props) {
  const [confirmClose, setConfirmClose] = useState(false);
  useEffect(() => {
    setConfirmClose(false);
  }, [result]);
  if (!result) return null;
  return (
    <Modal
      open
      title="Token 明文仅显示一次"
      ariaLabel="Token 明文仅显示一次"
      onClose={() => setConfirmClose(true)}
      width={620}
      closeOnBackdrop={false}
    >
      {confirmClose ? (
        <div className="collector-token-clear-confirm">
          <h3>请确认已安全保存明文 Token</h3>
          <p>关闭后，前端会立即清空本次明文，服务端无法再次读取。</p>
          <div>
            <Button variant="primary" onClick={onClear}>
              确认关闭并清空
            </Button>
            <Button onClick={() => setConfirmClose(false)}>返回复制</Button>
          </div>
        </div>
      ) : (
        <div className="collector-token-created">
          <div className="collector-token-warning">
            <strong>现在复制并保存</strong>
            <span>服务端只保存哈希；此明文关闭后不会再次显示。</span>
          </div>
          <code>{result.plaintext_token}</code>
          <dl>
            <div>
              <dt>名称</dt>
              <dd>{result.token.name}</dd>
            </div>
            <div>
              <dt>Scope</dt>
              <dd>{result.token.scopes.join("、")}</dd>
            </div>
            <div>
              <dt>Token ID</dt>
              <dd>{result.token.token_id}</dd>
            </div>
          </dl>
          <div className="collector-token-created__actions">
            <Button variant="primary" icon={Copy} onClick={() => onCopy(result.plaintext_token, "Token")}>
              复制 Token
            </Button>
            <Button onClick={() => setConfirmClose(true)}>关闭</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
