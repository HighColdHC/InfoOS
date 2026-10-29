import type { EnvironmentComponent } from "../../../../lib/types/collectionEnvironment";
import { ComponentCard } from "./ComponentCard";

export function ComponentOverview({
  components,
  openingChrome,
  onOpenChrome,
  checkingBridge,
  onCheckBridge,
}: {
  components: EnvironmentComponent[];
  openingChrome: boolean;
  onOpenChrome: () => void;
  checkingBridge: boolean;
  onCheckBridge: () => void;
}) {
  return (
    <section className="collector-environment-components" aria-label="系统组件">
      <div className="collector-environment-section-title">
        <div>
          <h2>系统组件</h2>
          <p>展示最近一次检查结果；检查 Bridge 只读取现有连接。</p>
        </div>
        <span>{components.length} 项</span>
      </div>
      <div className="collector-environment-component-grid">
        {components.map((component) => (
          <ComponentCard
            key={component.id}
            component={component}
            opening={openingChrome && component.id === "dedicated_chrome"}
            onOpen={onOpenChrome}
            checkingBridge={checkingBridge && component.id === "opencli_bridge"}
            onCheckBridge={onCheckBridge}
          />
        ))}
      </div>
    </section>
  );
}
