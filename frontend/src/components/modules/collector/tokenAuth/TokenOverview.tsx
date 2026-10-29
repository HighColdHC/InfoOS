import { Copy, Server } from "lucide-react";
import type { TokenOverview as Overview } from "../../../../lib/types/tokenAuth";
import { Button } from "../../../ui/primitives/Button";

export function TokenOverview({ overview, onCopy }: { overview: Overview; onCopy: (value: string, label: string) => void }) {
  const address = `${window.location.origin}${overview.interface_path}`;
  return (
    <section className="collector-token-overview" aria-label="插件 Interface">
      <div className="collector-token-overview__icon"><Server size={20} aria-hidden="true" /></div>
      <div><span>本机插件 Interface</span><code>{address}</code><small>版本 {overview.interface_version} · scope {overview.capabilities.join("、")}</small></div>
      <span className={`collector-token-service collector-token-service--${overview.service_status}`}>{overview.service_status === "ready" ? "服务正常" : "服务降级"}</span>
      <Button icon={Copy} onClick={() => onCopy(address, "Interface 地址")} aria-label="复制 Interface 地址">复制地址</Button>
    </section>
  );
}
