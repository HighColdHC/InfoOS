import "../../styles/_metric-strip.css";
import type { LucideIcon } from "lucide-react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

export type MetricItem = {
  label: string;
  value: string | number;
  icon: LucideIcon;
  description?: string;
  trend?: string;
  trendDirection?: "up" | "down" | "flat";
};

function TrendIcon({ direction }: { direction?: MetricItem["trendDirection"] }) {
  if (direction === "up") return <TrendingUp size={12} aria-hidden="true" />;
  if (direction === "down") return <TrendingDown size={12} aria-hidden="true" />;
  return <Minus size={12} aria-hidden="true" />;
}

export function MetricGrid({ metrics }: { metrics: MetricItem[] }) {
  return (
    <section
      className="metric-grid"
      data-cols={metrics.length}
      aria-label="基本统计指标"
    >
      {metrics.map((item, i) => {
        const Icon = item.icon;
        const hasTrend = Boolean(item.trend);
        return (
          <div className="metric-card" key={`${item.label}-${i}`}>
            <div className="metric-card__head">
              <span className="metric-card__icon" aria-hidden="true">
                <Icon size={16} />
              </span>
              {hasTrend ? (
                <span className="metric-card__trend">
                  <TrendIcon direction={item.trendDirection} />
                  <span>{item.trend}</span>
                </span>
              ) : null}
            </div>
            <strong className="metric-card__value">{item.value}</strong>
            <span className="metric-card__label">
              {item.label}
              {item.description ? <small className="metric-card__description">{item.description}</small> : null}
            </span>
          </div>
        );
      })}
    </section>
  );
}
