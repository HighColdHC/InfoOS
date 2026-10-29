import { errorMessage } from "../../../lib/format";
import "../../../styles/_collector-token.css";
import { useState } from "react";
import {
  createAdminToken,
  replayAdminTokenWebhooks,
  revokeAdminToken,
  rotateAdminToken,
  testAdminTokenWebhook,
  updateAdminToken,
} from "../../../lib/api/tokenAuth";
import type {
  RequestLogItem,
  TokenAdminRecord,
  TokenCreateResult,
  TokenConfigurationInput,
} from "../../../lib/types/tokenAuth";
import { useToast } from "../../ui/Toast";
import { TokenTabs, type TokenTab } from "./tokenAuth/TokenTabs";
import { CreateTokenDialog } from "./tokenAuth/CreateTokenDialog";
import { TokenCreatedDialog } from "./tokenAuth/TokenCreatedDialog";
import { TokenActionConfirmDialog, type TokenAction } from "./tokenAuth/TokenActionConfirmDialog";
import { RequestDetailDialog } from "./tokenAuth/RequestDetailDialog";
import { RequestLogsPanel } from "./tokenAuth/RequestLogsPanel";
import { TokenManagementPanel } from "./tokenAuth/TokenManagementPanel";
import { useTokenAuthData } from "./tokenAuth/useTokenAuthData";


export function TokenAuthenticationModule() {
  const toast = useToast();
  const [tab, setTab] = useState<TokenTab>(tabFromUrl);
  const { overview, tokens, authLoading, authError, setAuthRefresh, logs, logsLoading, logsError,
    queryDraft, status, timeRange, offset, setLogsRefresh, setQuery, setQueryDraft, setStatus,
    setTimeRange, setOffset, requestDetail, detailLoading, detailError, setDetailId,
    setRequestDetail, setDetailError } = useTokenAuthData(tab);
  const [createOpen, setCreateOpen] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [editingToken, setEditingToken] = useState<TokenAdminRecord | null>(null);
  const [createdResult, setCreatedResult] = useState<TokenCreateResult | null>(null);
  const [tokenAction, setTokenAction] = useState<TokenAction | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [webhookBusyTokenId, setWebhookBusyTokenId] = useState<string | null>(null);
  function selectTab(next: TokenTab) {
    setTab(next);
    const params = new URLSearchParams(window.location.search);
    params.set("menu", "tokens");
    params.set("tab", next);
    window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}${window.location.hash}`);
  }

  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.pushToast({ kind: "success", message: `${label}已复制` });
    } catch (error) {
      toast.pushToast({ kind: "error", message: `复制失败：${errorMessage(error)}` });
    }
  }

  async function saveToken(input: TokenConfigurationInput) {
    if (createBusy) return;
    setCreateBusy(true);
    setCreateError(null);
    try {
      if (editingToken) {
        await updateAdminToken(editingToken.token_id, input);
        setEditingToken(null);
        toast.pushToast({ kind: "success", message: `${input.name} 的 Webhook 配置已保存` });
      } else {
        const result = await createAdminToken(input);
        setCreateOpen(false);
        setCreatedResult(result);
        toast.pushToast({ kind: "success", message: `Token 已创建：${result.token.token_id}` });
      }
      setAuthRefresh((value) => value + 1);
    } catch (error) {
      setCreateError(errorMessage(error));
    } finally {
      setCreateBusy(false);
    }
  }

  async function confirmTokenAction() {
    if (!tokenAction || actionBusy) return;
    setActionBusy(true);
    setActionError(null);
    try {
      if (tokenAction.kind === "revoke") {
        await revokeAdminToken(tokenAction.token.token_id);
        toast.pushToast({ kind: "success", message: `${tokenAction.token.name} 已撤销` });
      } else {
        const result = await rotateAdminToken(tokenAction.token.token_id);
        setCreatedResult(result);
        toast.pushToast({ kind: "success", message: `${tokenAction.token.name} 已轮换` });
      }
      setTokenAction(null);
      setAuthRefresh((value) => value + 1);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setActionBusy(false);
    }
  }

  async function testWebhook(token: TokenAdminRecord) {
    if (webhookBusyTokenId) return;
    setWebhookBusyTokenId(token.token_id);
    try {
      const result = await testAdminTokenWebhook(token.token_id);
      toast.pushToast({ kind: "success", message: `${token.name} 连接正常（HTTP ${result.status_code}）` });
      setAuthRefresh((value) => value + 1);
    } catch (error) {
      toast.pushToast({ kind: "error", message: `Webhook 测试失败：${errorMessage(error)}` });
    } finally {
      setWebhookBusyTokenId(null);
    }
  }

  async function replayWebhook(token: TokenAdminRecord) {
    if (webhookBusyTokenId) return;
    setWebhookBusyTokenId(token.token_id);
    try {
      const result = await replayAdminTokenWebhooks(token.token_id);
      toast.pushToast({ kind: "success", message: `已重新排队 ${result.replayed_count} 条失败通知` });
      setAuthRefresh((value) => value + 1);
    } catch (error) {
      toast.pushToast({ kind: "error", message: `Webhook 重放失败：${errorMessage(error)}` });
    } finally {
      setWebhookBusyTokenId(null);
    }
  }

  function openDetail(item: RequestLogItem) {
    setDetailId(item.request_id);
  }
  function closeDetail() {
    setDetailId(null);
    setRequestDetail(null);
    setDetailError(null);
  }

  return (
    <section className="collector-token-page" aria-label="Token 认证工作区">
      <TokenTabs active={tab} onSelect={selectTab} />
      {tab === "auth" ? (
        <TokenManagementPanel
          overview={overview} tokens={tokens} authLoading={authLoading} authError={authError}
          webhookBusyTokenId={webhookBusyTokenId} actionBusy={actionBusy} tokenAction={tokenAction}
          setAuthRefresh={setAuthRefresh} setCreateError={setCreateError} setCreateOpen={setCreateOpen}
          setEditingToken={setEditingToken} setActionError={setActionError} setTokenAction={setTokenAction}
          onCopy={copy} onTestWebhook={testWebhook} onReplayWebhook={replayWebhook}
        />
      ) : (
        <RequestLogsPanel
          logs={logs} logsLoading={logsLoading} logsError={logsError}
          queryDraft={queryDraft} status={status} timeRange={timeRange} offset={offset}
          setLogsRefresh={setLogsRefresh} setOffset={setOffset} setQuery={setQuery}
          setQueryDraft={setQueryDraft} setStatus={setStatus} setTimeRange={setTimeRange}
          onDetail={openDetail}
        />
      )}
      <CreateTokenDialog
        open={createOpen || Boolean(editingToken)}
        token={editingToken}
        busy={createBusy}
        error={createError}
        onClose={() => {
          if (!createBusy) {
            setCreateOpen(false);
            setEditingToken(null);
          }
        }}
        onCreate={saveToken}
      />
      <TokenCreatedDialog result={createdResult} onCopy={copy} onClear={() => setCreatedResult(null)} />
      <TokenActionConfirmDialog
        action={tokenAction}
        busy={actionBusy}
        error={actionError}
        onClose={() => {
          if (!actionBusy) setTokenAction(null);
        }}
        onConfirm={confirmTokenAction}
      />
      <RequestDetailDialog
        detail={requestDetail}
        loading={detailLoading}
        error={detailError}
        onClose={closeDetail}
        onCopy={copy}
      />
    </section>
  );
}

function tabFromUrl(): TokenTab {
  return new URLSearchParams(window.location.search).get("tab") === "requests" ? "requests" : "auth";
}
