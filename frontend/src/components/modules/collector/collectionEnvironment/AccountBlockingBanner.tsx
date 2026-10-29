import type { PlatformAccount } from "../../../../lib/types/collectionEnvironment";

export function AccountBlockingBanner({ accounts }: { accounts: PlatformAccount[] }) {
  const blocked = accounts.filter(
    (account) => ["needs_login", "failed"].includes(account.status) && account.affected_source_count > 0,
  );
  if (!blocked.length) return null;
  const total = blocked.reduce((sum, account) => sum + account.affected_source_count, 0);
  return (
    <aside className="collector-environment-blocking" role="alert">
      <strong>{total} 个来源受登录状态影响</strong>
      <span>{blocked.map((account) => `${account.label} ${account.affected_source_count}`).join(" · ")}</span>
    </aside>
  );
}
