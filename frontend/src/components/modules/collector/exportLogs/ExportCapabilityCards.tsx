import { Button } from "../../../ui/primitives/Button";
import type { ExportKind } from "../../../../lib/types/exportLogs";

const cards: Array<{ kind: ExportKind; title: string; text: string }> = [
  { kind: "data", title: "来源与卡片数据", text: "JSON 数据交换；可选运行、内容块、附件与脱敏日志。" },
  { kind: "backup", title: "完整本地备份", text: "一致性 SQLite 快照与附件索引；媒体实体可选。" },
  { kind: "diagnostic", title: "脱敏诊断包", text: "日志、环境快照与选定 Source Run，不包含正文隐私。" },
];

export function ExportCapabilityCards({ onOpen }: { onOpen: (kind: ExportKind) => void }) {
  return (
    <section className="collector-export-capabilities" aria-label="导出能力">
      {cards.map((card) => (
        <article key={card.kind}>
          <span>{card.kind.toUpperCase()}</span>
          <h3>{card.title}</h3>
          <p>{card.text}</p>
          <Button
            variant="secondary"
            size="sm"
            type="button"
            aria-label={`配置${card.title}`}
            onClick={() => onOpen(card.kind)}
          >
            配置范围
          </Button>
        </article>
      ))}
    </section>
  );
}
