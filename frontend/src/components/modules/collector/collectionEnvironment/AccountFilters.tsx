export function AccountFilters({
  query,
  status,
  onQuery,
  onStatus,
}: {
  query: string;
  status: string;
  onQuery: (value: string) => void;
  onStatus: (value: string) => void;
}) {
  return (
    <div className="collector-environment-filters">
      <label>
        <span>搜索账号</span>
        <input
          aria-label="搜索平台账号"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder="平台或会话标识"
        />
      </label>
      <label>
        <span>账号状态</span>
        <select aria-label="账号状态筛选" value={status} onChange={(event) => onStatus(event.target.value)}>
          <option value="all">全部</option>
          <option value="verified">已登录</option>
          <option value="needs_login">需要登录</option>
          <option value="failed">检查失败</option>
          <option value="unknown">尚未检查</option>
          <option value="public">公网直采</option>
        </select>
      </label>
    </div>
  );
}
