import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Modal } from "../../../ui/Modal";
import { Button } from "../../../ui/primitives/Button";
import { Loader2, RefreshCw, Search, SlidersHorizontal, Telescope } from "lucide-react";
import type { CollectorPlatformOption } from "../../../../lib/platforms";
import { SegmentedControl } from "../../../ui/primitives/SegmentedControl";
import { PlatformMark } from "../../../ui/PlatformMark";
import type { CardPreviewSort } from "../../../../lib/types/cardPreview";

type Props = {
  children?: ReactNode;
  filtersActive?: number;
  queryDraft: string;
  query: string;
  platform: string;
  status: string;
  sort: CardPreviewSort;
  recommended: boolean;
  total: number;
  processingTotal: number;
  loading: boolean;
  platformOptions: CollectorPlatformOption[];
  onQueryDraftChange: (value: string) => void;
  onSearch: () => void;
  onPlatformChange: (value: string) => void;
  onStatusChange: (value: string) => void;
  onSortChange: (value: CardPreviewSort) => void;
  onRecommendedChange: (value: boolean) => void;
  onRefresh: () => void;
  onResearch: () => void;
};
export function CardPreviewToolbar(props: Props) {
  const [mobile, setMobile] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const closeFilters = useCallback(() => setFiltersOpen(false), []);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 720px)");
    const update = () => { setMobile(query.matches); if (!query.matches) setFiltersOpen(false); };
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  const filtersActive = props.filtersActive ?? 0;
  const moreRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (event: PointerEvent) => {
      const more = moreRef.current;
      if (more?.open && event.target instanceof Node && !more.contains(event.target)) more.open = false;
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);
  const selectedCount = filtersActive + Number(Boolean(props.platform)) + Number(Boolean(props.status))
    + Number(props.recommended)
    + Number(props.sort !== "published_desc" && !(props.recommended && props.sort === "relevance_desc"));
  const controls = (
    <>
      <label className="card-reader-toolbar__select">
        <span className="sr-only">排序</span>
        <select
          aria-label="排序"
          value={props.sort}
          onChange={(event) => props.onSortChange(event.target.value as Props["sort"])}
        >
          <option value="published_desc">最新发布</option>
          <option value="updated_desc">最近更新</option>
          {props.recommended ? <option value="relevance_desc">按相关度</option> : null}
        </select>
      </label>
      <label className="card-reader-toolbar__select card-reader-toolbar__secondary">
        <span className="sr-only">状态</span>
        <select aria-label="状态" value={props.status} onChange={(event) => props.onStatusChange(event.target.value)}>
          <option value="">全部</option>
          <option value="partial">部分缺失</option>
          <option value="processing">处理中</option>
        </select>
      </label>
      <label className="card-reader-toolbar__recommended">
        <input type="checkbox" role="switch" checked={props.recommended}
          onChange={(event) => props.onRecommendedChange(event.target.checked)} />
        只看已推荐
      </label>
      <button
        type="button"
        className="card-reader-toolbar__refresh"
        onClick={props.onRefresh}
        aria-label="刷新卡片"
        disabled={props.loading}
        data-loading={props.loading || undefined}
      >
        <RefreshCw size={15} />
      </button>
      <span className="card-reader-toolbar__total" aria-live="polite">
        {props.loading ? <Loader2 size={12} className="btn__icon--spinner" aria-hidden="true" /> : null}
        共 {props.total} 条
      </span>
    </>
  );
  const platforms = (
    <div className="card-reader-toolbar__platforms">
      <SegmentedControl
        className="card-reader-toolbar__chips"
        options={[
          { value: "", label: "全部" },
          ...props.platformOptions.map((option) => ({ ...option, icon: <PlatformMark platform={option.value} size="sm" /> })),
        ]}
        value={props.platform}
        onChange={props.onPlatformChange}
        label="平台筛选"
      />
    </div>
  );
  return (
    <form
      className="card-reader-toolbar"
      role="search"
      aria-label="卡片筛选"
      onSubmit={(event) => {
        event.preventDefault();
        props.onSearch();
      }}
    >
      <div className="card-reader-toolbar__main">
        <label className="card-reader-toolbar__search">
          <Search size={16} aria-hidden="true" />
          <input
            type="search"
            value={props.queryDraft}
            onChange={(event) => props.onQueryDraftChange(event.target.value)}
            placeholder="搜索标题、作者、正文或来源 URL"
            aria-label="搜索信息卡"
          />
          <kbd aria-hidden="true">/</kbd>
        </label>
        {mobile ? <Button type="button" variant="secondary" icon={SlidersHorizontal}
          aria-haspopup="dialog" aria-expanded={filtersOpen} onClick={() => setFiltersOpen(true)}>
          筛选（{selectedCount}）
        </Button> : controls}
      </div>
      {!mobile ? <div className="card-reader-toolbar__row">
        {platforms}
        <div className="card-reader-toolbar__aside">
          {props.children ? (
            <details className="card-reader-toolbar__more" ref={moreRef} data-active={filtersActive > 0 || undefined}>
              <summary>
                <SlidersHorizontal size={14} aria-hidden="true" />
                日期与来源
                {filtersActive ? <span className="card-reader-toolbar__more-count">{filtersActive}</span> : null}
              </summary>
              <div className="card-reader-toolbar__more-body">{props.children}</div>
            </details>
          ) : null}
          {props.processingTotal > 0 ? (
            <button
              type="button"
              className="card-reader-toolbar__processing"
              onClick={() => props.onStatusChange("processing")}
            >
              {props.processingTotal} 张处理中
            </button>
          ) : null}
          {props.query ? (
            <Button type="button" variant="secondary" size="sm" icon={Telescope} onClick={props.onResearch}>
              去平台调研“{props.query}”
            </Button>
          ) : null}
        </div>
      </div> : null}
      <Modal open={mobile && filtersOpen} onClose={closeFilters} title="筛选信息卡"
        actions={<Button type="button" onClick={closeFilters}>查看结果</Button>}>
        <div className="card-reader-filters">
          <div className="card-reader-filters__controls">{controls}</div>
          {platforms}
          {props.children}
          <div className="card-reader-filters__actions">
            {props.processingTotal > 0 ? <button type="button" className="card-reader-toolbar__processing"
              onClick={() => props.onStatusChange("processing")}>{props.processingTotal} 张处理中</button> : null}
            {props.query ? <Button type="button" variant="secondary" size="sm" icon={Telescope} onClick={props.onResearch}>
              去平台调研“{props.query}”
            </Button> : null}
          </div>
        </div>
      </Modal>
    </form>
  );
}
