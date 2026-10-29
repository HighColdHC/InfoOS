import { useContext, useState } from "react";
import {
  BookOpenText, Compass, DatabaseBackup, Files, KeyRound, ListTree, Monitor, Moon, MoreHorizontal, Rss, ShieldCheck, Sun, Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "./primitives/Button";
import { Badge } from "./primitives/Badge";
import { BrandMark } from "./BrandMark";
import { ThemeContext } from "./ThemeProvider";
import { latestBriefDate } from "../../lib/briefDate";

export type SidebarBadgeCounts = Record<string, number | undefined>;

export function Sidebar({
  activeKey,
  onSelect,
  badgeCounts = {},
}: {
  activeKey: string;
  onSelect: (key: string) => void;
  badgeCounts?: SidebarBadgeCounts;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const menuButton = (menu: (typeof SIDEBAR_MENUS)[number]) => {
    const count = badgeCounts[menu.key];
    const active = menu.key === activeKey;
    return (
      <Button
        key={menu.key}
        variant="ghost"
        fullWidth
        icon={menu.icon}
        aria-label={menu.label}
        aria-pressed={active}
        onClick={() => {
          onSelect(menu.key);
          setMoreOpen(false);
        }}
        className={active ? "menu-button active" : "menu-button"}
      >
        {active ? <span className="menu-button__indicator" aria-hidden="true" /> : null}
        <span className="menu-label" aria-hidden="true">
          {menu.label}
        </span>
        {count ? <Badge count={count} variant="count" /> : null}
        {menu.hotkey ? <kbd className="menu-button__hotkey" aria-hidden="true">G {menu.hotkey}</kbd> : null}
      </Button>
    );
  };
  return (
    <aside className="sidebar collector-sidebar" aria-label="主菜单">
      <div className="brand-block">
        <BrandMark />
        <div className="brand-block__text">
          <div className="brand-mark">InfoOS</div>
          <div className="brand-subtitle">个人情报系统 · 本地运行</div>
        </div>
      </div>
      <nav className="menu-list" aria-label="一级页面">
        <span className="menu-group-label">内容</span>
        {menuButton(SIDEBAR_MENUS[0])}
        <span className="menu-group-label">采集</span>
        {SIDEBAR_MENUS.slice(1, 4).map(menuButton)}
        <span className="menu-group-label">系统</span>
        {SIDEBAR_MENUS.slice(4).map(menuButton)}
        <Button
          variant="ghost"
          icon={MoreHorizontal}
          aria-label="更多"
          aria-expanded={moreOpen}
          className="menu-button collector-mobile-more"
          onClick={() => setMoreOpen((value) => !value)}
        >
          <span className="menu-label">更多</span>
        </Button>
      </nav>
      {moreOpen ? (
        <div className="collector-more-backdrop" onClick={() => setMoreOpen(false)}>
          <div
            className="collector-more-panel"
            role="dialog"
            aria-label="更多页面"
            onClick={(event) => event.stopPropagation()}
          >
            {SIDEBAR_MENUS.slice(3).map(menuButton)}
            <ReadingLinks />
          </div>
        </div>
      ) : null}
      <div className="sidebar__footer">
        <ReadingLinks />
        <ThemeSwitch />
        <div className="collector-sidebar__service" aria-label="本机服务地址">
          <span><i className="live-dot" aria-hidden="true" />本机服务</span>
          <code>{window.location.host || window.location.origin}</code>
        </div>
      </div>
    </aside>
  );
}

function ReadingLinks() {
  return (
    <div className="sidebar__reading" aria-label="阅读面">
      <a className="sidebar__reading-link" href="/r/opportunities"><Compass size={15} aria-hidden="true" /><span>商机跟进</span></a>
      <a className="sidebar__reading-link" href="/r/browse">
        <Compass size={15} aria-hidden="true" />
        <span>漫步阅读</span>
      </a>
      <a className="sidebar__reading-link" href={`/r/brief/${latestBriefDate()}`}>
        <BookOpenText size={15} aria-hidden="true" />
        <span>最新日报</span>
      </a>
    </div>
  );
}

function ThemeSwitch() {
  const theme = useContext(ThemeContext);
  if (!theme) return null;
  const options = [
    { value: "light", label: "浅色", icon: Sun },
    { value: "dark", label: "深色", icon: Moon },
  ] as const;
  return (
    <div className="theme-switch" role="radiogroup" aria-label="外观">
      <Monitor size={13} aria-hidden="true" className="theme-switch__icon" />
      {options.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={theme.theme === value}
          aria-label={label}
          title={label}
          onClick={() => {
            const apply = () => theme.setTheme(value);
            const doc = document as Omit<Document, "startViewTransition"> & { startViewTransition?: (callback: () => void) => unknown };
            if (doc.startViewTransition && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
              doc.documentElement.dataset.themeSwitching = "true";
              const transition = doc.startViewTransition(apply) as { finished?: Promise<unknown> };
              void transition.finished?.finally(() => delete doc.documentElement.dataset.themeSwitching);
            } else apply();
          }}
        >
          <Icon size={14} aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}

export const SIDEBAR_MENUS: { key: string; label: string; icon: LucideIcon; description: string; hotkey?: string }[] = [
  { key: "cards", label: "信息卡", icon: Files, hotkey: "C", description: "采集到的信息卡与 agent 写回的评估，按来源与时间漫读。" },
  { key: "sources", label: "信息源", icon: Rss, hotkey: "S", description: "维护持续采集的平台来源、频率与健康度。" },
  { key: "tasks", label: "采集任务", icon: ListTree, hotkey: "T", description: "来源运行、按需调研与持久任务队列的执行轨迹。" },
  { key: "environment", label: "采集环境", icon: Wrench, hotkey: "E", description: "运行组件、专用浏览器与各平台登录态。" },
  { key: "tokens", label: "Token 认证", icon: KeyRound, hotkey: "K", description: "为 agent、插件与 Webhook 签发设备级访问令牌。" },
  { key: "account", label: "账号与安全", icon: ShieldCheck, description: "管理面的访问账号与安全设置。" },
  { key: "logs", label: "导出与日志", icon: DatabaseBackup, hotkey: "L", description: "导出本地数据，追溯系统运行日志。" },
];
