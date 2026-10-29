import type { BlockedSource } from "../../../../lib/types/sourceRuns";

const reasonLabels: Record<string, string> = {
  platform_not_logged_in: "平台登录已失效",
  platform_challenge_required: "平台要求验证码或安全验证",
  browser_page_load_failed: "平台页面加载失败",
  browser_session_render_blocked: "平台页面被风控或无法正常渲染",
  browser_session_not_ready: "专用 Chrome 会话未就绪",
  task_blocked: "采集任务被阻断",
  source_unavailable: "所列来源的账号或订阅地址不存在/不可用，请核对并修改或停用这些来源",
};

const reasonPriority: Record<string, number> = {
  platform_challenge_required: 5,
  platform_not_logged_in: 4,
  browser_session_render_blocked: 3,
  browser_session_not_ready: 2,
  browser_page_load_failed: 2,
  task_blocked: 1,
  source_unavailable: 0,
};

function groupByPlatform(sources: BlockedSource[]) {
  const groups = new Map<string, { key: string; platform: string; reason: string; sourceNames: Map<string, string> }>();
  for (const source of sources) {
    const platform = source.platform || "unknown";
    const key = source.reason === "source_unavailable" ? `${platform}:source_unavailable` : platform;
    const current = groups.get(key);
    if (!current) {
      groups.set(key, {
        key,
        platform,
        reason: source.reason,
        sourceNames: new Map([[source.source_id, source.source_name]]),
      });
      continue;
    }
    current.sourceNames.set(source.source_id, source.source_name);
    if ((reasonPriority[source.reason] ?? 0) > (reasonPriority[current.reason] ?? 0)) {
      current.reason = source.reason;
    }
  }
  return [...groups.values()];
}

export function RunBlockingBanner({ sources }: { sources: BlockedSource[] }) {
  if (!sources.length) return null;
  const groups = groupByPlatform(sources);
  return (
    <aside className="collector-task-blocking" role="alert">
      <div>
        <strong>采集前置条件需要处理</strong>
        {groups.map((group) => {
          const names = [...group.sourceNames.values()];
          return (
            <p key={group.key}>
              <b>{names.length === 1 ? names[0] : `${names.length} 个来源`}</b>
              <span>{group.platform}</span>：{reasonLabels[group.reason] ?? group.reason}
            </p>
          );
        })}
      </div>
      <div>
        {groups.map((group) => (
          <a
            key={group.key}
            className="btn btn--secondary btn--sm"
            aria-label={`处理 ${group.platform} ${group.reason === "source_unavailable" ? "来源" : "登录"}`}
            href={group.reason === "source_unavailable" ? "?menu=sources" : `?menu=environment&platform=${encodeURIComponent(group.platform)}`}
          >
            {group.reason === "source_unavailable" ? "检查来源" : "处理登录"}
          </a>
        ))}
      </div>
    </aside>
  );
}
