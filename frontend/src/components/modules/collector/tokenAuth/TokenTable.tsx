import type { TokenAdminRecord } from "../../../../lib/types/tokenAuth";
import { Button } from "../../../ui/primitives/Button";

type Props = {
  items: TokenAdminRecord[];
  busyTokenId: string | null;
  onEdit: (token: TokenAdminRecord) => void;
  onRevoke: (token: TokenAdminRecord) => void;
  onRotate: (token: TokenAdminRecord) => void;
  onTestWebhook: (token: TokenAdminRecord) => void;
  onReplayWebhook: (token: TokenAdminRecord) => void;
};

export function TokenTable({ items, busyTokenId, onEdit, onRevoke, onRotate, onTestWebhook, onReplayWebhook }: Props) {
  return (
    <div className="collector-token-table-wrap">
      <table className="collector-token-table" aria-label="Token 列表">
        <thead>
          <tr>
            <th>名称</th>
            <th>脱敏前缀</th>
            <th>Scope</th>
            <th>Webhook</th>
            <th>创建时间</th>
            <th>最近使用</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((token) => {
            const active = token.status === "active" && !token.revoked_at;
            const busy = busyTokenId === token.token_id;
            return (
              <tr key={token.token_id}>
                <td>
                  <strong>{token.name}</strong>
                  <small>{token.token_id}</small>
                </td>
                <td>
                  <code>{token.token_prefix}</code>
                </td>
                <td>
                  {token.scopes.map((scope) => (
                    <span className="collector-token-scope" key={scope}>
                      {scope}
                    </span>
                  ))}
                </td>
                <td>
                  {token.webhook_enabled ? (
                    <WebhookState token={token} />
                  ) : (
                    <span className="collector-token-webhook">未启用</span>
                  )}
                </td>
                <td>{formatDate(token.created_at)}</td>
                <td>{formatDate(token.last_used_at)}</td>
                <td>
                  <span className={`collector-token-status ${active ? "is-active" : "is-revoked"}`}>
                    {active ? "有效" : token.status === "replaced" ? "已轮换" : "已撤销"}
                  </span>
                </td>
                <td>
                  <div className="collector-token-actions">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!active || busy}
                      onClick={() => onEdit(token)}
                      aria-label={`编辑 ${token.name}`}
                    >
                      编辑
                    </Button>
                    {token.webhook_enabled ? (
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={!active || busy}
                          onClick={() => onTestWebhook(token)}
                          aria-label={`测试 ${token.name} Webhook`}
                        >
                          测试
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={!active || busy || token.webhook_delivery_status !== "active"}
                          onClick={() => onReplayWebhook(token)}
                          aria-label={`重放 ${token.name} Webhook`}
                        >
                          重放
                        </Button>
                      </>
                    ) : null}
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!active || busy}
                      onClick={() => onRotate(token)}
                      aria-label={`轮换 ${token.name}`}
                    >
                      轮换
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={!active || busy}
                      onClick={() => onRevoke(token)}
                      aria-label={`撤销 ${token.name}`}
                    >
                      撤销
                    </Button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function WebhookState({ token }: { token: TokenAdminRecord }) {
  const paused = token.webhook_delivery_status === "paused";
  const pending = token.webhook_delivery_status === "pending_verification";
  return (
    <span
      className={`collector-token-webhook is-enabled${paused || pending ? " is-paused" : ""}`}
      title={token.webhook_url ?? undefined}
    >
      {paused ? "已暂停" : pending ? "待验证" : "运行中"}
      <small>
        {paused
          ? `连续失败 ${token.webhook_consecutive_failures} 次`
          : pending
            ? "配置接收端后点击测试"
            : token.webhook_last_success_at
              ? `上次成功 ${formatDate(token.webhook_last_success_at)}`
              : "等待首次投递"}
      </small>
      {token.webhook_last_error ? <small className="is-error">{token.webhook_last_error}</small> : null}
    </span>
  );
}

function formatDate(value: string | null) {
  if (!value) return "从未";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(date);
}
