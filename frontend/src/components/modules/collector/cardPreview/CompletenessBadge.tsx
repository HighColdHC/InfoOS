import { completenessLabel } from "../../../../lib/cardPresentation";

export function CompletenessBadge({ value }: { value: string }) {
  return <span className={`collector-completeness collector-completeness--${value}`}>{completenessLabel(value)}</span>;
}
