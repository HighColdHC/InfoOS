import { lazy, Suspense, useEffect, type ComponentType, type ReactElement } from "react";
import { HeaderBar, type HeaderBarStatus } from "./components/ui/HeaderBar";
import { Sidebar, SIDEBAR_MENUS } from "./components/ui/Sidebar";
import { OnboardingModal } from "./components/ui/OnboardingModal";
import { formatLocalDateTime } from "./lib/format";
import { CollectionEnvironmentModule } from "./components/modules/collector/CollectionEnvironmentModule";

const loaders = {
  cards: () => import("./components/modules/collector/CardPreviewModule").then(module => ({ default: module.CardPreviewModule })),
  sources: () => import("./components/modules/collector/SourceManagementModule").then(module => ({ default: module.SourceManagementModule })),
  tasks: () => import("./components/modules/collector/CollectionTasksModule").then(module => ({ default: module.CollectionTasksModule })),
  tokens: () => import("./components/modules/collector/TokenAuthenticationModule").then(module => ({ default: module.TokenAuthenticationModule })),
  account: () => import("./components/modules/collector/LanAccountModule").then(module => ({ default: module.LanAccountModule })),
  logs: () => import("./components/modules/collector/ExportLogsModule").then(module => ({ default: module.ExportLogsModule })),
};
const CardPreviewModule = lazy(loaders.cards);
const SourceManagementModule = lazy(loaders.sources);
const CollectionTasksModule = lazy(loaders.tasks);
const TokenAuthenticationModule = lazy(loaders.tokens);
const LanAccountModule = lazy(loaders.account);
const ExportLogsModule = lazy(loaders.logs);

export const COLLECTOR_MENU_KEYS = ["cards", "sources", "tasks", "environment", "tokens", "account", "logs"] as const;
export type CollectorMenuKey = typeof COLLECTOR_MENU_KEYS[number];

type StaticCollectorMenuKey = Exclude<CollectorMenuKey, "environment">;

const modules: Record<StaticCollectorMenuKey, ComponentType> = {
  cards: CardPreviewModule,
  sources: SourceManagementModule,
  tasks: CollectionTasksModule,
  tokens: TokenAuthenticationModule,
  account: LanAccountModule,
  logs: ExportLogsModule,
};

export type AppShellProps = {
  activeMenu: CollectorMenuKey;
  onSelectMenu: (key: string) => void;
  status: HeaderBarStatus;
  statusError?: string | null;
  latestCollectionAt: string | null;
  onRefresh: () => void;
  showOnboarding: boolean;
  onCloseOnboarding: () => void;
  onStartSetup: () => Promise<void>;
};

export function AppShell(props: AppShellProps): ReactElement {
  const activeMeta = SIDEBAR_MENUS.find((menu) => menu.key === props.activeMenu) ?? SIDEBAR_MENUS[0];
  useEffect(() => {
    // Warm the other pages' chunks once the first page has painted so menu
    // switches land on rendered content instead of a loading fallback.
    const warm = () => Object.values(loaders).forEach((load) => void load().catch(() => undefined));
    // Idle scheduling is an optional browser capability, not an SSR/jsdom guard.
    const scheduler = window as Omit<Window, "requestIdleCallback" | "cancelIdleCallback"> & {
      requestIdleCallback?: Window["requestIdleCallback"];
      cancelIdleCallback?: Window["cancelIdleCallback"];
    };
    if (!scheduler.requestIdleCallback) return;
    const handle = scheduler.requestIdleCallback(warm, { timeout: 4000 });
    return () => scheduler.cancelIdleCallback?.(handle);
  }, []);
  return (
    <main className="app-shell collector-shell">
      <Sidebar activeKey={props.activeMenu} onSelect={props.onSelectMenu} />
      <section className={`workspace collector-workspace${props.activeMenu === "cards" ? " collector-workspace--cards" : ""}`}>
        <HeaderBar
          title={activeMeta.label}
          description={activeMeta.description}
          status={props.status}
          errorMessage={props.statusError ?? undefined}
          onShowEnvironment={() => props.onSelectMenu("environment")}
          onRefresh={props.onRefresh}
          rightSlot={<span className="collector-latest">最近采集成功：{formatLocalDateTime(props.latestCollectionAt) || "暂无"}</span>}
        />
        <Suspense fallback={<div className="workspace-loading" role="status"><span className="spinner" aria-hidden="true" />正在加载…</div>}>
          <ActiveCollectorModule key={props.activeMenu} activeMenu={props.activeMenu} onStartSetup={props.onStartSetup} />
        </Suspense>
      </section>
      {props.showOnboarding ? <OnboardingModal onClose={props.onCloseOnboarding} /> : null}
    </main>
  );
}

function ActiveCollectorModule({
  activeMenu,
  onStartSetup,
}: {
  activeMenu: CollectorMenuKey;
  onStartSetup: () => Promise<void>;
}): ReactElement {
  if (activeMenu === "environment") {
    return <CollectionEnvironmentModule onStartSetup={onStartSetup} />;
  }
  const ActiveModule = modules[activeMenu];
  return <ActiveModule />;
}
