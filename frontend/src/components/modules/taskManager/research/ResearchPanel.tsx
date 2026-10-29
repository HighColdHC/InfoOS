import { useEffect, useMemo, useState } from "react";
import { createResearchRun, fetchResearchCapabilities, fetchResearchRuns } from "../../../../lib/api/research";
import type {
  ResearchPlatformCapability,
  ResearchSort,
  ResearchRunRequest,
  ResearchRunSummary,
} from "../../../../lib/types/research";
import { Select } from "../../../ui/primitives/Select";
import { Button } from "../../../ui/primitives/Button";
import { ResearchRunDetail } from "./ResearchRunDetail";
import { researchCapabilityLabel, runStatusLabel } from "../../../../lib/labels";

const sortLabels: Record<ResearchSort, string> = { general: "综合", latest: "最新", earliest: "最早", "most-liked": "最多点赞", "most-commented": "最多评论", "most-collected": "最多收藏" };
const defaultLimit = 20;
const terms = (value: string) => [
  ...new Set(
    value
      .split(/[\n,，]/)
      .map((item) => item.trim())
      .filter(Boolean),
  ),
];

export function ResearchPanel() {
  const [capabilities, setCapabilities] = useState<ResearchPlatformCapability[]>([]);
  const [runs, setRuns] = useState<ResearchRunSummary[]>([]);
  const initialKeywords = new URLSearchParams(window.location.search).get("keywords")?.trim() ?? "";
  const [name, setName] = useState(initialKeywords);
  const [keywords, setKeywords] = useState(initialKeywords);
  const [selected, setSelected] = useState<string[]>([]);
  const [limits, setLimits] = useState<Record<string, number>>({});
  const [sorts, setSorts] = useState<Record<string, ResearchSort>>({});
  const [sourceIds, setSourceIds] = useState<Record<string, string>>({});
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);

  async function refresh() {
    const [catalog, recent] = await Promise.all([fetchResearchCapabilities(), fetchResearchRuns()]);
    setCapabilities(catalog);
    setRuns(recent.research_runs);
  }
  useEffect(() => {
    void refresh()
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, []);

  const selectedCapabilities = useMemo(
    () => capabilities.filter((item) => selected.includes(item.platform)),
    [capabilities, selected],
  );
  function toggle(platform: string, checked: boolean) {
    setSelected((current) => (checked ? [...current, platform] : current.filter((item) => item !== platform)));
  }
  function enabled(capability: ResearchPlatformCapability) {
    return capability.research.status !== "unavailable";
  }
  function validate(): string | null {
    if (!name.trim()) return "请填写调研名称。";
    if (!terms(keywords).length) return "请至少填写一个关键词。";
    if (!selectedCapabilities.length) return "请至少选择一个可用平台。";
    if (Boolean(dateStart) !== Boolean(dateEnd)) return "日期范围需要同时填写开始日期和结束日期。";
    if (dateStart && dateEnd && dateStart > dateEnd) return "开始日期不能晚于结束日期。";
    for (const item of selectedCapabilities)
      if (item.research.requires_source_selection && !terms(sourceIds[item.platform] ?? "").length)
        return `${item.label} 需要填写已有来源 ID。`;
    return null;
  }
  async function submit() {
    const invalid = validate();
    if (invalid) {
      setError(invalid);
      return;
    }
    const payload: ResearchRunRequest = {
      name: name.trim(),
      keywords: terms(keywords),
      platforms: selectedCapabilities.map((item) => ({
        platform: item.platform,
        max_items: Math.min(Math.max(1, limits[item.platform] ?? defaultLimit), item.research.max_items_limit),
        sort: sorts[item.platform] ?? item.research.default_sort,
        options: item.research.requires_source_selection ? { source_ids: terms(sourceIds[item.platform] ?? "") } : {},
      })),
    };
    if (dateStart || dateEnd)
      payload.date_range = {
        start_date: dateStart,
        end_date: dateEnd,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
      };
    setSubmitting(true);
    setError(null);
    try {
      const result = await createResearchRun(payload, crypto.randomUUID());
      setRuns((current) => [
        result.research_run,
        ...current.filter((item) => item.research_run_id !== result.research_run.research_run_id),
      ]);
      setDetailId(result.research_run.research_run_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="research-panel" aria-label="调研功能">
      <div className="research-heading">
        <div>
          <h2>发起调研</h2>
          <p>一次提交可将多个关键词发往多个平台；任务会进入现有采集与信息卡流水线。</p>
        </div>
      </div>
      {loading ? <p role="status">正在读取平台调研能力…</p> : null}
      {error ? (
        <p role="alert" className="research-error">
          {error}
        </p>
      ) : null}
      {!loading ? (
        <form
          className="research-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label>
            调研名称
            <input
              aria-label="调研名称"
              value={name}
              maxLength={120}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：AI 网站出海"
            />
          </label>
          <label>
            关键词
            <textarea
              aria-label="调研关键词"
              value={keywords}
              onChange={(event) => setKeywords(event.target.value)}
              placeholder="每行或用逗号分隔一个关键词"
            />
          </label>
          <fieldset>
            <legend>可调研平台</legend>
            <div className="research-platform-picker">
              {capabilities.map((item) => (
                <label key={item.platform} className={!enabled(item) ? "is-unavailable" : ""}>
                  <input
                    aria-label={item.label}
                    type="checkbox"
                    checked={selected.includes(item.platform)}
                    disabled={!enabled(item)}
                    onChange={(event) => toggle(item.platform, event.target.checked)}
                  />
                  <span>
                    <strong>{item.label}</strong>
                    <small>
                      {researchCapabilityLabel(item.research.status)}
                      {item.research.limitation ? ` · ${item.research.limitation}` : ""}
                    </small>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          {selectedCapabilities.length ? (
            <div className="research-platform-options">
              {selectedCapabilities.map((item) => (
                <div key={item.platform}>
                  <strong>{item.label}</strong>
                  <label>
                    每平台条数
                    <input
                      aria-label={`${item.label} 每平台条数`}
                      type="number"
                      min="1"
                      max={item.research.max_items_limit}
                      value={limits[item.platform] ?? defaultLimit}
                      onChange={(event) =>
                        setLimits((current) => ({ ...current, [item.platform]: Number(event.target.value) }))
                      }
                    />
                  </label>
                  {item.research.sort_options.length > 1 ? (
                    <Select label="排序方式" aria-label={`${item.label} 排序方式`}
                      value={sorts[item.platform] ?? item.research.default_sort}
                      onChange={(event) => setSorts((current) => ({ ...current, [item.platform]: event.target.value as ResearchSort }))}>
                      {item.research.sort_options.map((sort) => <option key={sort} value={sort}>{sortLabels[sort]}</option>)}
                    </Select>
                  ) : null}
                  {item.research.requires_source_selection ? (
                    <label>
                      已有来源 ID
                      <input
                        aria-label={`${item.label} 已有来源 ID`}
                        value={sourceIds[item.platform] ?? ""}
                        onChange={(event) =>
                          setSourceIds((current) => ({ ...current, [item.platform]: event.target.value }))
                        }
                        placeholder="逗号分隔"
                      />
                    </label>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
          <div className="research-date-range">
            <label>
              开始日期（可选）
              <input
                aria-label="开始日期"
                type="date"
                value={dateStart}
                onChange={(event) => setDateStart(event.target.value)}
              />
            </label>
            <label>
              结束日期（可选）
              <input
                aria-label="结束日期"
                type="date"
                value={dateEnd}
                onChange={(event) => setDateEnd(event.target.value)}
              />
            </label>
          </div>
          <div>
            <Button type="submit" loading={submitting}>
              提交调研任务
            </Button>
            <small>提交成功仅代表已排队，不代表采集或信息卡已完成。</small>
          </div>
        </form>
      ) : null}
      <section className="research-recent" aria-label="最近调研批次">
        <h3>最近调研批次</h3>
        {runs.length ? (
          <ul>
            {runs.map((run) => (
              <li key={run.research_run_id}>
                <div>
                  <strong>{run.name}</strong>
                  <small>
                    {runStatusLabel(run.status)} · {run.keywords.join("、")}
                  </small>
                </div>
                <Button size="sm" onClick={() => setDetailId(run.research_run_id)}>
                  查看详情
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p>还没有调研批次。</p>
        )}
      </section>
      {detailId ? <ResearchRunDetail researchRunId={detailId} onClose={() => setDetailId(null)} /> : null}
    </section>
  );
}
