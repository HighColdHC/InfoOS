import type { Dispatch, SetStateAction } from "react";
import { Plus, RefreshCw, ShieldCheck } from "lucide-react";
import type { TokenAdminRecord, TokenOverview as TokenOverviewType } from "../../../../lib/types/tokenAuth";
import { Button } from "../../../ui/primitives/Button";
import { TokenOverview } from "./TokenOverview";
import { TokenTable } from "./TokenTable";
import type { TokenAction } from "./TokenActionConfirmDialog";

type Props = {
  overview: TokenOverviewType | null;
  tokens: TokenAdminRecord[];
  authLoading: boolean;
  authError: string | null;
  webhookBusyTokenId: string | null;
  actionBusy: boolean;
  tokenAction: TokenAction | null;
  setAuthRefresh: Dispatch<SetStateAction<number>>;
  setCreateError: Dispatch<SetStateAction<string | null>>;
  setCreateOpen: Dispatch<SetStateAction<boolean>>;
  setEditingToken: Dispatch<SetStateAction<TokenAdminRecord | null>>;
  setActionError: Dispatch<SetStateAction<string | null>>;
  setTokenAction: Dispatch<SetStateAction<TokenAction | null>>;
  onCopy: (value: string, label: string) => void;
  onTestWebhook: (token: TokenAdminRecord) => void;
  onReplayWebhook: (token: TokenAdminRecord) => void;
};

export function TokenManagementPanel({ overview, tokens, authLoading, authError, webhookBusyTokenId,
  actionBusy, tokenAction, setAuthRefresh, setCreateError, setCreateOpen, setEditingToken,
  setActionError, setTokenAction, onCopy, onTestWebhook, onReplayWebhook }: Props) {
  return (
        <section id="collector-token-panel-auth" role="tabpanel" aria-labelledby="collector-token-tab-auth" className="collector-token-panel">
          <div className="collector-token-heading">
            <div>
              <h2>认证管理</h2>
              <p>为卡片读取、附件访问或浏览器捕获签发最小权限凭据。</p>
            </div>
            <Button
              variant="primary"
              icon={Plus}
              onClick={() => {
                setCreateError(null);
                setCreateOpen(true);
              }}
            >
              创建 Token
            </Button>
          </div>
          {authLoading && !overview ? (
            <div className="collector-token-state" role="status">
              正在读取 Token 配置…
            </div>
          ) : null}
          {authError ? (
            <div className="collector-token-state is-error" role="alert">
              Token 配置读取失败：{authError}
              <Button icon={RefreshCw} onClick={() => setAuthRefresh((value) => value + 1)}>
                重试
              </Button>
            </div>
          ) : null}
          {overview && !authError ? (
            <>
              <TokenOverview overview={overview} onCopy={onCopy} />
              <div className="collector-token-security">
                <ShieldCheck size={18} aria-hidden="true" />
                <span>
                  <strong>明文只显示一次</strong>服务端只保存哈希；日志不会记录 Authorization 或完整凭据。
                </span>
              </div>
              <TokenTable
                items={tokens}
                busyTokenId={webhookBusyTokenId ?? (actionBusy ? (tokenAction?.token.token_id ?? null) : null)}
                onEdit={(token) => {
                  setCreateError(null);
                  setEditingToken(token);
                }}
                onRevoke={(token) => {
                  setActionError(null);
                  setTokenAction({ kind: "revoke", token });
                }}
                onRotate={(token) => {
                  setActionError(null);
                  setTokenAction({ kind: "rotate", token });
                }}
                onTestWebhook={(token) => {
                  onTestWebhook(token);
                }}
                onReplayWebhook={(token) => {
                  onReplayWebhook(token);
                }}
              />
            </>
          ) : null}
        </section>
  );
}
