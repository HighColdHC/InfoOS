import type { SourceConfigurationPayload, SourceManagementItem } from "../../../../lib/types/sourceManagement";
import { Input } from "../../../ui/primitives/Input";
import { Select } from "../../../ui/primitives/Select";
import { parseZsxqKeywords } from "./sourceFormUtils";
import type { useSourceForm } from "./useSourceForm";

type Props = { state: ReturnType<typeof useSourceForm>; source: SourceManagementItem | null | undefined; editing: boolean; busy: boolean };

export function SourcePlatformFields({ state, source, editing, busy }: Props) {
  const { form, setForm, updateZsxqSearch } = state;
  return (
    <>
        {(form.platform === "xiaohongshu" || form.platform === "douyin") && form.source_type === "keyword" ? (
          <fieldset className="collector-source-form__ai">
            <legend>关键词搜索筛选</legend>
            <div className="collector-source-form__grid">
              <Select label="排序方式" aria-label="排序方式" disabled={busy}
                value={form.raw_metadata.keyword_search?.sort ?? "general"}
                onChange={(event) => setForm((current) => ({ ...current, raw_metadata: {
                  ...current.raw_metadata, keyword_search: { ...current.raw_metadata.keyword_search,
                    sort: event.target.value as NonNullable<SourceConfigurationPayload["raw_metadata"]["keyword_search"]>["sort"],
                  },
                } }))}>
                <option value="general">综合</option>
                <option value="latest">最新</option>
                <option value="most-liked">最多点赞</option>
                {form.platform === "xiaohongshu" ? <option value="most-commented">最多评论</option> : null}
                {form.platform === "xiaohongshu" ? <option value="most-collected">最多收藏</option> : null}
              </Select>
              <Select label="发布时间" aria-label="发布时间" disabled={busy}
                value={form.raw_metadata.keyword_search?.publish_time ?? "any"}
                onChange={(event) => setForm((current) => ({ ...current, raw_metadata: {
                  ...current.raw_metadata, keyword_search: { ...current.raw_metadata.keyword_search,
                    publish_time: event.target.value as NonNullable<SourceConfigurationPayload["raw_metadata"]["keyword_search"]>["publish_time"],
                  },
                } }))}>
                <option value="any">不限</option>
                <option value="day">一天内</option>
                <option value="week">一周内</option>
                <option value="half-year">半年内</option>
              </Select>
            </div>
          </fieldset>
        ) : null}
        {form.platform === "zsxq" && form.source_type === "keyword" ? (
          <fieldset className="collector-source-form__ai">
            <legend>生财有术站内搜索</legend>
            <div className="collector-source-form__grid">
              <Select
                label="排序方式"
                aria-label="排序方式"
                disabled={busy}
                value={form.raw_metadata.zsxq_search?.sort ?? "comprehensive"}
                onChange={(event) =>
                  updateZsxqSearch({
                    sort: event.target.value as NonNullable<
                      SourceConfigurationPayload["raw_metadata"]["zsxq_search"]
                    >["sort"],
                  })
                }
              >
                <option value="comprehensive">综合排序</option>
                <option value="newest">最新发布</option>
                <option value="earliest">最早发布</option>
                <option value="most-favorited">最多收藏</option>
                <option value="most-commented">最多评论</option>
                <option value="most-liked">最多点赞</option>
                <option value="most-coins">最多锚点</option>
                <option value="recent-coins">最新投锚</option>
              </Select>
              <Select
                label="文章类型"
                aria-label="文章类型"
                disabled={busy}
                value={form.raw_metadata.zsxq_search?.content_type ?? "all"}
                onChange={(event) =>
                  updateZsxqSearch({
                    content_type: event.target.value as NonNullable<
                      SourceConfigurationPayload["raw_metadata"]["zsxq_search"]
                    >["content_type"],
                  })
                }
              >
                <option value="all">全部</option>
                <option value="essence">精华</option>
                <option value="bid">中标</option>
                <option value="topic">主题帖</option>
                <option value="opportunity">风向标</option>
                <option value="square">广场</option>
              </Select>
              <Input
                label="开始日期"
                aria-label="开始日期"
                type="date"
                max={form.raw_metadata.zsxq_search?.end_date || undefined}
                disabled={busy}
                value={form.raw_metadata.zsxq_search?.start_date ?? ""}
                onChange={(event) => updateZsxqSearch({ start_date: event.target.value })}
              />
              <Input
                label="结束日期"
                aria-label="结束日期"
                type="date"
                min={form.raw_metadata.zsxq_search?.start_date || undefined}
                disabled={busy}
                value={form.raw_metadata.zsxq_search?.end_date ?? ""}
                onChange={(event) => updateZsxqSearch({ end_date: event.target.value })}
              />
            </div>
            <label className="collector-source-form__textarea">
              <span>补充关键词（可选）</span>
              <textarea
                aria-label="补充关键词（可选）"
                disabled={busy}
                value={(form.raw_metadata.zsxq_search?.keywords ?? []).join("\n")}
                onChange={(event) => updateZsxqSearch({ keywords: parseZsxqKeywords(event.target.value) })}
                placeholder={"每行一个，也可以用逗号分隔\n例如：web出海\nAI网站出海"}
              />
            </label>
            <p className="collector-source-form__ai-hint">
              来源地址或标识作为主关键词。系统逐个调用知识星球站内搜索，再按帖子 ID
              全局去重，并使用网站返回的时间游标继续分页。
            </p>
          </fieldset>
        ) : null}
        {form.platform === "douyin" && form.source_type === "creator" ? (
          <fieldset className="collector-source-form__ai">
            <legend>抖音评论</legend>
            <label className="collector-source-form__ai-toggle">
              <input
                type="checkbox"
                checked={form.raw_metadata.include_top_comments === true}
                disabled={busy}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    raw_metadata: { ...current.raw_metadata, include_top_comments: event.target.checked },
                  }))
                }
              />
              抓取作品评论
            </label>
            <p className="collector-source-form__ai-hint">
              开启后每个作品额外读取最多 50 条评论，按点赞数排序写入卡片；单次抓取耗时会明显变长。
            </p>
          </fieldset>
        ) : null}
        {editing ? (
          <div className="collector-source-form__connector">
            <span>连接器说明</span>
            <strong>{source?.connector_id || "由平台能力自动选择"}</strong>
            <small>连接器由 InfoOS 管理，不能在此页面修改。</small>
          </div>
        ) : null}
    </>
  );
}
