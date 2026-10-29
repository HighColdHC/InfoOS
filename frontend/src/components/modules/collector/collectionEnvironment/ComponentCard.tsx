import { Button } from "../../../ui/primitives/Button";
import type { EnvironmentComponent } from "../../../../lib/types/collectionEnvironment";
import { componentStatusLabel } from "./environmentViewModel";

export function ComponentCard({
  component,
  opening,
  onOpen,
  checkingBridge,
  onCheckBridge,
}: {
  component: EnvironmentComponent;
  opening: boolean;
  onOpen: () => void;
  checkingBridge: boolean;
  onCheckBridge: () => void;
}) {
  const canOpen = component.id === "dedicated_chrome";
  const isBridge = component.id === "opencli_bridge";
  const bridgeHint = component.detail_code === "daemon_unavailable"
    ? "OpenCLI daemon 未运行或无法访问"
    : component.detail_code === "bridge_disconnected"
      ? "Chrome 扩展未连接到 OpenCLI daemon"
      : component.detail_code === "bridge_check_failed"
        ? "连接检查失败，请重试"
        : component.status === "unknown" ? "点击检查 Bridge 确认连接" : null;
  return (
    <article className="collector-environment-component">
      <div className="collector-environment-component__head">
        <strong>{component.name}</strong>
        {canOpen ? (
          <Button
            variant="secondary"
            size="sm"
            type="button"
            aria-label="打开专用 Chrome"
            disabled={opening}
            onClick={onOpen}
          >
            {opening ? "打开中…" : "打开"}
          </Button>
        ) : isBridge ? (
          <Button variant="secondary" size="sm" type="button" disabled={checkingBridge} onClick={onCheckBridge}>
            {checkingBridge ? "检查中…" : "检查 Bridge"}
          </Button>
        ) : null}
      </div>
      {isBridge ? (
        <small>
          {bridgeHint ? <span>{bridgeHint}。 </span> : null}
          {component.last_checked_at ? `上次检查 ${component.last_checked_at}` : "连接尚未检查"}
        </small>
      ) : null}
      <div>
        <span className={`collector-environment-state is-${component.status}`}>
          {componentStatusLabel(component.status)}
        </span>
        <code>{component.version ?? "版本未知"}</code>
      </div>
    </article>
  );
}
