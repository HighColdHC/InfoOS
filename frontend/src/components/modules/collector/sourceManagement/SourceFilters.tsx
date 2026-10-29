import { useState } from "react";
import { Search, SlidersHorizontal } from "lucide-react";
import type { CollectorPlatformOption } from "../../../../lib/platforms";

type Props = {
  queryDraft: string;
  platform: string;
  status: string;
  platformOptions: CollectorPlatformOption[];
  onQueryDraftChange: (value: string) => void;
  onSearch: () => void;
  onPlatformChange: (value: string) => void;
  onStatusChange: (value: string) => void;
};

export function SourceFilters(props: Props) {
  const [filtersOpen, setFiltersOpen] = useState(false);
  const activeFilterCount =
    Number(Boolean(props.queryDraft)) + Number(Boolean(props.platform)) + Number(Boolean(props.status));
  return (
    <form
      className="collector-source-filters collector-filter-toolbar"
      role="search"
      aria-label="来源筛选"
      data-expanded={filtersOpen || undefined}
      onSubmit={(event) => {
        event.preventDefault();
        props.onSearch();
      }}
    >
      <div className="collector-source-filters__fields collector-filter-toolbar__filters">
        <label className="collector-source-search collector-filter-field collector-filter-field--search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="搜索信息源"
            placeholder="搜索名称、标识或来源 ID"
            value={props.queryDraft}
            onChange={(event) => props.onQueryDraftChange(event.target.value)}
          />
        </label>
        <label className="collector-filter-field">
          <span className="sr-only">来源平台</span>
          <select
            aria-label="来源平台"
            value={props.platform}
            onChange={(event) => props.onPlatformChange(event.target.value)}
          >
            <option value="">全部平台</option>
            {props.platformOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="collector-filter-field">
          <span className="sr-only">来源状态</span>
          <select
            aria-label="来源状态"
            value={props.status}
            onChange={(event) => props.onStatusChange(event.target.value)}
          >
            <option value="">全部状态</option>
            <option value="healthy">健康</option>
            <option value="needs_attention">需处理</option>
            <option value="disabled">已停用</option>
          </select>
        </label>
      </div>
      <div className="collector-source-filters__actions collector-filter-toolbar__actions">
        <button
          type="button"
          className="collector-filter-toolbar__toggle"
          aria-expanded={filtersOpen}
          onClick={() => setFiltersOpen((value) => !value)}
        >
          <SlidersHorizontal size={16} aria-hidden="true" />
          筛选{activeFilterCount ? ` ${activeFilterCount}` : ""}
        </button>
      </div>
    </form>
  );
}
