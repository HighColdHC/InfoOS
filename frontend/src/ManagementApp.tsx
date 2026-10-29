import { useCallback, useEffect, useState } from "react";
import { flushSync } from "react-dom";
import { loadSetupState, postSetupReset } from "./lib/api";
import { loadCollectorStatus } from "./lib/api/collectorStatus";
import { isOnboardingComplete } from "./lib/onboarding";
import { useAsyncEffect } from "./components/ui/hooks/useAsyncEffect";
import { useHotkeys } from "./components/ui/hooks/useHotkeys";
import { AppShell, COLLECTOR_MENU_KEYS, type CollectorMenuKey } from "./AppShell";
import type { HeaderBarStatus } from "./components/ui/HeaderBar";
import { navigateToMenu } from "./lib/navigation";

import "./styles/_shell.css";
import "./styles/_onboarding.css";
import "./styles/_diagnostic.css";
import "./styles/_collector-shell.css";
import "./styles/_surfaces.css";

function menuFromLocation(): CollectorMenuKey {
  const value = new URLSearchParams(window.location.search).get("menu");
  return COLLECTOR_MENU_KEYS.includes(value as CollectorMenuKey) ? value as CollectorMenuKey : "cards";
}

export default function ManagementApp({ refreshIntervalMs }: { refreshIntervalMs: number }) {
  const [activeMenu, setActiveMenu] = useState<CollectorMenuKey>(menuFromLocation);
  const [serviceStatus, setServiceStatus] = useState<HeaderBarStatus>("loading");
  const [latestCollectionAt, setLatestCollectionAt] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(false);

  useEffect(() => {
    const syncMenu = () => flushSync(() => setActiveMenu(menuFromLocation()));
    window.addEventListener("popstate", syncMenu);
    window.addEventListener("infoos:navigate", syncMenu);
    return () => { window.removeEventListener("popstate", syncMenu); window.removeEventListener("infoos:navigate", syncMenu); };
  }, []);

  const refreshStatus = useCallback(async (showLoading = false) => {
    if (showLoading) setServiceStatus("loading");
    try {
      const status = await loadCollectorStatus();
      setLatestCollectionAt(status.latest_collection_at);
      const labels: Record<string, string> = {
        sources_overdue: "部分来源未按计划完成采集",
        runtime_unhealthy: "运行组件异常，请查看环境页",
      };
      setStatusError(status.reasons.map(reason => labels[reason] ?? reason).join("；") || null);
      setServiceStatus(status.service_status === "ready" ? "sync" : status.service_status);
    } catch (error) {
      setStatusError(error instanceof Error ? error.message : "本机服务状态读取失败");
      setServiceStatus("error");
    }
  }, []);

  useEffect(() => {
    void refreshStatus(true);
    if (refreshIntervalMs <= 0) return undefined;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void refreshStatus();
    }, refreshIntervalMs);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void refreshStatus();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refreshIntervalMs, refreshStatus]);

  useAsyncEffect(async (signal) => {
    try {
      const setup = await loadSetupState();
      if (!signal.aborted && setup.current_step !== "complete" && setup.current_step !== "skipped") {
        setShowOnboarding(true);
      }
    } catch {
      if (!signal.aborted && !isOnboardingComplete()) setShowOnboarding(true);
    }
  }, []);

  const selectMenu = useCallback((key: string) => {
    if (!COLLECTOR_MENU_KEYS.includes(key as CollectorMenuKey)) return;
    const menu = key as CollectorMenuKey;
    if (menu === activeMenu) return;
    navigateToMenu(menu);
  }, [activeMenu]);

  const startSetup = useCallback(async () => {
    await postSetupReset();
    setShowOnboarding(true);
  }, []);

  useHotkeys({
    "g c": () => selectMenu("cards"),
    "g s": () => selectMenu("sources"),
    "g t": () => selectMenu("tasks"),
    "g e": () => selectMenu("environment"),
    "g k": () => selectMenu("tokens"),
    "g l": () => selectMenu("logs"),
  });

  return (
    <AppShell
      activeMenu={activeMenu}
      onSelectMenu={selectMenu}
      status={serviceStatus}
      statusError={statusError}
      latestCollectionAt={latestCollectionAt}
      onRefresh={() => void refreshStatus(true)}
      showOnboarding={showOnboarding}
      onCloseOnboarding={() => setShowOnboarding(false)}
      onStartSetup={startSetup}
    />
  );
}
