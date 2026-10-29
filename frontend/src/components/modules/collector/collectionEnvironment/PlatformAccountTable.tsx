import { Button } from "../../../ui/primitives/Button";
import { PlatformMark } from "../../../ui/PlatformMark";
import type { PlatformAccount } from "../../../../lib/types/collectionEnvironment";
import { accountStatusLabel, dateTime } from "./environmentViewModel";

type Props = {
  accounts: PlatformAccount[];
  checkingPlatform: string | null;
  loginPlatform: string | null;
  onCheck: (account: PlatformAccount) => void;
  onLogin: (account: PlatformAccount) => void;
};

export function PlatformAccountTable({ accounts, checkingPlatform, loginPlatform, onCheck, onLogin }: Props) {
  return (
    <div className="collector-environment-table-scroll">
      <table className="collector-environment-table" aria-label="平台账号">
        <thead>
          <tr>
            <th>平台</th>
            <th>账号状态</th>
            <th>浏览器能力</th>
            <th>会话标识</th>
            <th>最近检查</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <tr key={account.platform}>
              <td>
                <div className="platform-cell">
                  <PlatformMark platform={account.platform} />
                  <div>
                    <strong>{account.label}</strong>
                    <small>{account.platform}</small>
                  </div>
                </div>
              </td>
              <td>
                <span className={`collector-environment-account-state is-${account.status}`}>
                  {accountStatusLabel(account.status)}
                </span>
                {account.affected_source_count ? <small>影响 {account.affected_source_count} 个来源</small> : null}
              </td>
              <td>
                <code>{account.browser_capability}</code>
              </td>
              <td>
                <code>{account.session_id ?? "无需会话"}</code>
              </td>
              <td>{dateTime(account.last_checked_at)}</td>
              <td>
                <div className="collector-environment-row-actions">
                  <Button
                    variant="secondary"
                    size="sm"
                    type="button"
                    aria-label={`状态检查 ${account.label}`}
                    disabled={checkingPlatform === account.platform}
                    onClick={() => onCheck(account)}
                  >
                    {checkingPlatform === account.platform ? "检查中…" : "状态检查"}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    type="button"
                    aria-label={`去登录 ${account.label}`}
                    disabled={loginPlatform === account.platform}
                    onClick={() => onLogin(account)}
                  >
                    {loginPlatform === account.platform ? "打开中…" : "去登录"}
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
