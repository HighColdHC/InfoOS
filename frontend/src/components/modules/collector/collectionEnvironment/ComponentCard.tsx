import { Button } from "../../../ui/primitives/Button";
import type { EnvironmentComponent } from "../../../../lib/types/collectionEnvironment";
import { componentStatusLabel } from "./environmentViewModel";

export function ComponentCard({
  component,
  opening,
  onOpen,
}: {
  component: EnvironmentComponent;
  opening: boolean;
  onOpen: () => void;
}) {
  const canOpen = component.id === "dedicated_chrome";
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
        ) : null}
      </div>
      <div>
        <span className={`collector-environment-state is-${component.status}`}>
          {componentStatusLabel(component.status)}
        </span>
        <code>{component.version ?? "版本未知"}</code>
      </div>
    </article>
  );
}
