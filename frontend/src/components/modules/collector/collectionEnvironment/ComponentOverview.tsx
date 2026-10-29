import type { EnvironmentComponent } from "../../../../lib/types/collectionEnvironment";
import { ComponentCard } from "./ComponentCard";

export function ComponentOverview({
  components,
  openingChrome,
  onOpenChrome,
}: {
  components: EnvironmentComponent[];
  openingChrome: boolean;
  onOpenChrome: () => void;
}) {
  return (
    <section className="collector-environment-components" aria-label="系统组件">
      <div className="collector-environment-section-title">
        <div>
          <h2>系统组件</h2>
          <p>只读展示最近一次持久化安装快照。</p>
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
          />
        ))}
      </div>
    </section>
  );
}
