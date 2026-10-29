import type { SourceConfigurationPayload, SourceManagementItem, SourcePlatformCapability, SourceTypeCapability } from "../../../../lib/types/sourceManagement";

export function toPayload(source: SourceManagementItem): SourceConfigurationPayload {
  return {
    platform: source.platform,
    source_type: source.source_type,
    identifier: source.identifier,
    display_name: source.display_name,
    cadence_hours: source.cadence_hours,
    max_items_per_run: source.max_items_per_run,
    enabled: source.enabled,
    notes: source.notes,
    raw_metadata: {
      ...source.raw_metadata,
      auto_enrich_media: source.auto_enrich_media,
      ...feedSettingsFor(),
      ...(source.platform === "zsxq" && source.source_type === "keyword"
        ? {
            zsxq_search: normalizeZsxqSearch(
              source.raw_metadata.zsxq_search ?? source.raw_metadata.scys_search,
            ),
          }
        : {}),
    },
  };
}

export function normalizeFeedSettings(form: SourceConfigurationPayload): SourceConfigurationPayload {
  return { ...form, raw_metadata: { ...form.raw_metadata, ...feedSettingsFor() } };
}

function feedSettingsFor(): Pick<SourceConfigurationPayload["raw_metadata"], "feed_provider" | "feed_content_mode"> {
  return { feed_provider: "generic", feed_content_mode: "feed_preferred" };
}

function normalizeZsxqSearch(value: unknown): NonNullable<SourceConfigurationPayload["raw_metadata"]["zsxq_search"]> {
  if (!value || typeof value !== "object")
    return { sort: "comprehensive", content_type: "all", keywords: [], start_date: "", end_date: "" };
  const record = value as Record<string, unknown>;
  return {
    sort:
      typeof record.sort === "string"
        ? (record.sort as NonNullable<SourceConfigurationPayload["raw_metadata"]["zsxq_search"]>["sort"])
        : "comprehensive",
    content_type:
      typeof record.content_type === "string"
        ? (record.content_type as NonNullable<
            SourceConfigurationPayload["raw_metadata"]["zsxq_search"]
          >["content_type"])
        : "all",
    keywords: Array.isArray(record.keywords)
      ? record.keywords.filter((item): item is string => typeof item === "string")
      : [],
    start_date: typeof record.start_date === "string" ? record.start_date : "",
    end_date: typeof record.end_date === "string" ? record.end_date : "",
  };
}

export function parseZsxqKeywords(value: string): string[] {
  const keywords: string[] = [];
  const seen = new Set<string>();
  for (const part of value.split(/[\n,，]+/)) {
    const keyword = part.trim();
    const identity = keyword.toLocaleLowerCase();
    if (!keyword || seen.has(identity)) continue;
    seen.add(identity);
    keywords.push(keyword);
  }
  return keywords;
}

export function defaultsFor(
  capability: SourcePlatformCapability,
  current: SourceConfigurationPayload,
  sourceTypeName = capability.source_types.at(0)?.source_type ?? "",
): SourceConfigurationPayload {
  const sourceType: SourceTypeCapability | undefined = capability.source_types.find(
    (item) => item.source_type === sourceTypeName,
  );
  return {
    ...current,
    platform: capability.platform,
    raw_metadata: {
      ...current.raw_metadata,
      ...(current.platform !== capability.platform ? { keyword_search: undefined } : {}),
    },
    source_type: sourceTypeName,
    identifier:
      sourceType?.identifier_mode === "select" ? (sourceType.identifier_options.at(0)?.value ?? "") : current.identifier,
    cadence_hours: sourceType?.default_cadence_hours ?? capability.cadence_options.at(0) ?? current.cadence_hours,
    max_items_per_run: sourceType?.default_max_items ?? capability.default_max_items,
  };
}
