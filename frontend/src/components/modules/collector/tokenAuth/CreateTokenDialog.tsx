import { useEffect, useState } from "react";
import type { TokenAdminRecord, TokenConfigurationInput, TokenScope } from "../../../../lib/types/tokenAuth";
import { Modal } from "../../../ui/Modal";
import { Button } from "../../../ui/primitives/Button";
import { Input } from "../../../ui/primitives/Input";

type Props = {
  open: boolean;
  token: TokenAdminRecord | null;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onCreate: (input: TokenConfigurationInput) => void;
};

export function CreateTokenDialog({ open, token, busy, error, onClose, onCreate }: Props) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<TokenScope[]>(["cards:read", "assets:read"]);
  const [webhookEnabled, setWebhookEnabled] = useState(false);
  const [includeBackfill, setIncludeBackfill] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState("");
  useEffect(() => {
    if (open) {
      setName(token?.name ?? "");
      setScopes(token?.scopes ?? ["cards:read", "assets:read"]);
      setWebhookEnabled(token?.webhook_enabled ?? false);
      setIncludeBackfill(token?.webhook_include_backfill ?? false);
      setWebhookUrl(token?.webhook_url ?? "");
    }
  }, [open, token]);
  function toggle(scope: TokenScope, checked: boolean) {
    setScopes((current) => (checked ? [...new Set([...current, scope])] : current.filter((item) => item !== scope)));
    if (scope === "cards:read" && !checked) setWebhookEnabled(false);
  }
  const webhookAllowed = scopes.includes("cards:read");
  return (
    <Modal
      open={open}
      title={token ? "编辑 Token" : "创建 Token"}
      ariaLabel={token ? "编辑 Token" : "创建 Token"}
      onClose={onClose}
      busy={busy}
      width={520}
    >
      <form
        className="collector-token-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim() && scopes.length && (!webhookEnabled || (webhookAllowed && webhookUrl.trim())))
            onCreate({
              name: name.trim(),
              scopes,
              webhook_enabled: webhookEnabled,
              ...(includeBackfill || token?.webhook_include_backfill
                ? { webhook_include_backfill: includeBackfill } : {}),
              webhook_url: webhookEnabled ? webhookUrl.trim() : null,
            });
        }}
      >
        {error ? (
          <p className="collector-token-error" role="alert">
            {error}
          </p>
        ) : null}
        <Input
          label="Token 名称"
          aria-label="Token 名称"
          required
          maxLength={80}
          value={name}
          disabled={busy}
          onChange={(event) => setName(event.target.value)}
        />
        {!token ? (
          <Button
            size="sm"
            onClick={() => {
              setName("Save to InfoOS");
              setScopes(["captures:write"]);
              setWebhookEnabled(false);
              setIncludeBackfill(false);
              setWebhookUrl("");
            }}
          >
            使用浏览器捕获预设
          </Button>
        ) : null}
        <fieldset>
          <legend>最小权限 Scope</legend>
          <label>
            <input
              type="checkbox"
              aria-label="cards:read"
              checked={scopes.includes("cards:read")}
              disabled={busy}
              onChange={(event) => toggle("cards:read", event.target.checked)}
            />
            <span>
              <strong>cards:read</strong>
              <small>读取卡片目录与详情</small>
            </span>
          </label>
          <label>
            <input
              type="checkbox"
              aria-label="assets:read"
              checked={scopes.includes("assets:read")}
              disabled={busy}
              onChange={(event) => toggle("assets:read", event.target.checked)}
            />
            <span>
              <strong>assets:read</strong>
              <small>读取卡片附件</small>
            </span>
          </label>
          <label>
            <input
              type="checkbox"
              aria-label="captures:write"
              checked={scopes.includes("captures:write")}
              disabled={busy}
              onChange={(event) => toggle("captures:write", event.target.checked)}
            />
            <span>
              <strong>captures:write</strong>
              <small>只提交浏览器手动捕获</small>
            </span>
          </label>
          {(["intelligence:read", "profile:write", "evaluation:write", "feedback:write", "collect:write", "sources:write", "verification:write"] as const).map((scope) => (
            <label key={scope}>
              <input type="checkbox" aria-label={scope} checked={scopes.includes(scope)} disabled={busy}
                onChange={(event) => toggle(scope, event.target.checked)} />
              <span><strong>{scope}</strong><small>{scope === "intelligence:read" ? "通过 MCP 检索和读取情报" : scope === "profile:write" ? "更新关注画像" : scope === "evaluation:write" ? "通过 MCP 写回卡片评估" : scope === "collect:write" ? "通过 MCP 发起调研、提交链接捕获" : scope === "sources:write" ? "通过 MCP 新增、修改、启停来源（仅交互式 agent）" : scope === "verification:write" ? "写入商机核实记录与跟进状态（仅交互式 agent）" : "预留：提交反馈"}</small></span>
            </label>
          ))}
        </fieldset>
        {!scopes.length ? (
          <p className="collector-token-error" role="alert">
            至少选择一个 scope。
          </p>
        ) : null}
        <fieldset>
          <legend>Webhook 通知（可选）</legend>
          <label>
            <input
              type="checkbox"
              aria-label="启用 Webhook"
              checked={webhookEnabled}
              disabled={busy || !webhookAllowed}
              onChange={(event) => setWebhookEnabled(event.target.checked)}
            />
            <span>
              <strong>启用 Webhook</strong>
              <small>{webhookAllowed ? "卡片最终稳定且有增量时通知客户端拉取。" : "Webhook 需要 cards:read。"}</small>
            </span>
          </label>
          {webhookEnabled ? (
            <label>
              <input
                type="checkbox"
                aria-label="包含历史卡片"
                checked={includeBackfill}
                disabled={busy}
                onChange={(event) => setIncludeBackfill(event.target.checked)}
              />
              <span><strong>包含历史卡片</strong><small>默认只通知新发现的卡片。</small></span>
            </label>
          ) : null}
          {webhookEnabled ? (
            <Input
              label="Webhook URL"
              aria-label="Webhook URL"
              type="url"
              required
              disabled={busy}
              placeholder="https://example.com/infoos/webhook"
              value={webhookUrl}
              onChange={(event) => setWebhookUrl(event.target.value)}
              hint={
                token
                  ? "保存时会使用当前 Token 签名发送 webhook.test。"
                  : "创建后先把只显示一次的 Token 配置到接收端，再点击“测试”启用投递。"
              }
            />
          ) : null}
        </fieldset>
        <div className="collector-token-form__actions">
          <Button
            type="submit"
            variant="primary"
            loading={busy}
            disabled={!name.trim() || !scopes.length || (webhookEnabled && (!webhookAllowed || !webhookUrl.trim()))}
          >
            {token ? "保存 Token 配置" : "创建 Token"}
          </Button>
          <Button onClick={onClose} disabled={busy}>
            取消
          </Button>
        </div>
      </form>
    </Modal>
  );
}
