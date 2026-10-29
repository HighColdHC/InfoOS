import { Button } from "../../../ui/primitives/Button";
import { Modal } from "../../../ui/Modal";
import type { PlatformAccount } from "../../../../lib/types/collectionEnvironment";
import { accountStatusLabel, dateTime } from "./environmentViewModel";

type Props = {
  account: PlatformAccount | null;
  checking: boolean;
  loggingIn: boolean;
  error: string | null;
  onClose: () => void;
  onLogin: () => void;
};

export function AccountStatusDialog({ account, checking, loggingIn, error, onClose, onLogin }: Props) {
  return (
    <Modal
      open={Boolean(account)}
      onClose={onClose}
      title={account ? `账号状态检查 · ${account.label}` : "账号状态检查"}
      ariaLabel={account ? `账号状态检查 · ${account.label}` : "账号状态检查"}
      width={620}
      busy={checking || loggingIn}
      actions={
        <>
          <Button variant="ghost" size="md" type="button" disabled={checking || loggingIn} onClick={onClose}>
            关闭
          </Button>
          <Button variant="primary" size="md" type="button" disabled={checking || loggingIn} onClick={onLogin}>
            {loggingIn ? "正在打开…" : "在专用 Chrome 中登录"}
          </Button>
        </>
      }
    >
      {account ? (
        <div className="collector-environment-dialog">
          {checking ? (
            <div className="collector-environment-checking" role="status">
              正在检查 {account.label} 的专用会话…
            </div>
          ) : null}
          {error ? (
            <div className="collector-environment-dialog-error" role="alert">
              {error}
            </div>
          ) : null}
          <dl>
            <div>
              <dt>账号状态</dt>
              <dd>
                <span className={`collector-environment-account-state is-${account.status}`}>
                  {accountStatusLabel(account.status)}
                </span>
              </dd>
            </div>
            <div>
              <dt>浏览器能力</dt>
              <dd>
                <code>{account.browser_capability}</code>
              </dd>
            </div>
            <div>
              <dt>专用会话</dt>
              <dd>
                <code>{account.session_id ?? "无需会话"}</code>
              </dd>
            </div>
            <div>
              <dt>最近检查</dt>
              <dd>{dateTime(account.last_checked_at)}</dd>
            </div>
            <div>
              <dt>受影响来源</dt>
              <dd>{account.affected_source_count} 个</dd>
            </div>
            <div>
              <dt>错误说明</dt>
              <dd>{account.error ?? account.action_hint ?? "无"}</dd>
            </div>
          </dl>
        </div>
      ) : null}
    </Modal>
  );
}
