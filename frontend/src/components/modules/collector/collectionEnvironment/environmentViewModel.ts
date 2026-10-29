import type { EnvironmentComponentStatus, PlatformAccountStatus } from "../../../../lib/types/collectionEnvironment";

export function componentStatusLabel(status: EnvironmentComponentStatus): string {
  return ({ installed: "已安装", configured: "已配置", missing: "未安装", unknown: "尚未检查", failed: "检查失败" } as Record<string, string>)[status] ?? status;
}

export function accountStatusLabel(status: PlatformAccountStatus): string {
  return ({ verified: "已登录", needs_login: "需要登录", unknown: "尚未检查", public: "公网直采", failed: "检查失败" } as Record<string, string>)[status] ?? status;
}

export function dateTime(value: string | null): string {
  if (!value) return "尚未检查";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}
