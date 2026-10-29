import { SegmentedControl } from "../../../ui/primitives/SegmentedControl";

export type TokenTab = "auth" | "requests";
const OPTIONS: { value: TokenTab; label: string }[] = [
  { value: "auth", label: "认证管理" },
  { value: "requests", label: "请求日志" },
];

export function TokenTabs({ active, onSelect }: { active: TokenTab; onSelect: (tab: TokenTab) => void }) {
  return <SegmentedControl options={OPTIONS} value={active} onChange={onSelect} label="Token 页面"
    tabIdPrefix="collector-token-tab-" panelIdPrefix="collector-token-panel-" />;
}
