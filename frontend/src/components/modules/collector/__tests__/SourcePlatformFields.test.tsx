import { useEffect } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SourcePlatformFields } from "../sourceManagement/SourcePlatformFields";
import { defaultsFor } from "../sourceManagement/sourceFormUtils";
import type { SourceConfigurationPayload } from "../../../../lib/types/sourceManagement";
import { useSourceForm } from "../sourceManagement/useSourceForm";

function Form({ platform, sourceType = "keyword" }: { platform: string; sourceType?: string }) {
  const state = useSourceForm(undefined);
  useEffect(() => {
    state.setForm((current) => ({ ...current, platform, source_type: sourceType,
      raw_metadata: { ...current.raw_metadata, keep: "existing" } }));
  }, [platform, sourceType]);
  return <>
    <SourcePlatformFields state={state} source={undefined} editing={false} busy={false} />
    <output data-testid="metadata">{JSON.stringify(state.form.raw_metadata)}</output>
  </>;
}

describe("关键词搜索筛选", () => {
  it.each(["xiaohongshu", "douyin"])("%s renders platform options and preserves metadata on changes", (platform) => {
    render(<Form platform={platform} />);
    expect(screen.getByLabelText("排序方式")).toHaveValue("general");
    expect(screen.getByLabelText("发布时间")).toHaveValue("any");
    expect(Boolean(screen.queryByRole("option", { name: "最多评论" }))).toBe(platform === "xiaohongshu");
    expect(Boolean(screen.queryByRole("option", { name: "最多收藏" }))).toBe(platform === "xiaohongshu");
    fireEvent.change(screen.getByLabelText("排序方式"), { target: { value: "most-liked" } });
    fireEvent.change(screen.getByLabelText("发布时间"), { target: { value: "week" } });
    expect(JSON.parse(screen.getByTestId("metadata").textContent!)).toMatchObject({
      keep: "existing", keyword_search: { sort: "most-liked", publish_time: "week" },
    });
  });

  it("hides filters for creator sources", () => {
    render(<Form platform="xiaohongshu" sourceType="creator" />);
    expect(screen.queryByLabelText("排序方式")).not.toBeInTheDocument();
  });
});


it("clears platform-specific filters when changing platforms", () => {
  const current: SourceConfigurationPayload = {
    platform: "xiaohongshu", source_type: "keyword", identifier: "AI", display_name: "AI",
    cadence_hours: 5, max_items_per_run: 20, enabled: true,
    raw_metadata: { auto_enrich_media: true, ai_processing: { enabled: false },
      feed_provider: "generic", feed_content_mode: "feed_preferred",
      keyword_search: { sort: "most-collected", publish_time: "week" } },
  };
  const capability = { platform: "douyin", label: "抖音", source_types: [{ source_type: "keyword", label: "关键词", default_cadence_hours: 5, default_max_items: 20, identifier_mode: "text" as const, identifier_options: [], identifier_hint: null, requires_login: "no", login_verifiable: "n/a", max_items_limit: 30 }], cadence_options: [5], default_max_items: 20, max_items_limit: 30 };
  expect(defaultsFor(capability, current).raw_metadata.keyword_search).toBeUndefined();
  expect(defaultsFor({ ...capability, platform: "xiaohongshu" }, current).raw_metadata.keyword_search).toEqual(current.raw_metadata.keyword_search);
});
