import { useEffect, useMemo, useState } from "react";
import { fetchSourcePlatformCapabilities } from "../../../../lib/api/sourceManagement";
import type { SourceConfigurationPayload, SourceManagementItem, SourcePlatformCapability } from "../../../../lib/types/sourceManagement";
import { defaultsFor, normalizeFeedSettings, toPayload } from "./sourceFormUtils";

const blank: SourceConfigurationPayload = {
  platform: "",
  source_type: "",
  identifier: "",
  display_name: "",
  cadence_hours: 6,
  max_items_per_run: 20,
  enabled: true,
  raw_metadata: {
    auto_enrich_media: true,
    feed_provider: "generic",
    feed_content_mode: "feed_preferred",
  },
};

export function useSourceForm(source: SourceManagementItem | null | undefined) {
  const open = source !== undefined;
  const [catalog, setCatalog] = useState<SourcePlatformCapability[]>([]);
  const [form, setForm] = useState<SourceConfigurationPayload>(blank);
  const [catalogError, setCatalogError] = useState(false);
  useEffect(() => {
    if (!open) return;
    setForm(source ? toPayload(source) : blank);
    const controller = new AbortController();
    setCatalogError(false);
    void fetchSourcePlatformCapabilities(controller.signal)
      .then((items) => {
        setCatalog(items);
        if (!source && items[0]) setForm((current) => (current.platform ? current : defaultsFor(items[0], current)));
      })
      .catch((caught: unknown) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) setCatalogError(true);
      });
    return () => controller.abort();
  }, [open, source]);

  const capability = useMemo(() => catalog.find((item) => item.platform === form.platform), [catalog, form.platform]);
  const sourceType = capability?.source_types.find((item) => item.source_type === form.source_type);
  function changePlatform(platform: string) {
    const next = catalog.find((item) => item.platform === platform);
    setForm((current) =>
      normalizeFeedSettings(
        next ? defaultsFor(next, { ...current, platform, identifier: "" }) : { ...current, platform },
      ),
    );
  }

  function changeSourceType(sourceType: string) {
    setForm((current) =>
      capability
        ? defaultsFor(capability, { ...current, source_type: sourceType, identifier: "" }, sourceType)
        : { ...current, source_type: sourceType },
    );
  }
  function updateZsxqSearch(next: Partial<NonNullable<SourceConfigurationPayload["raw_metadata"]["zsxq_search"]>>) {
    setForm((current) => ({
      ...current,
      raw_metadata: { ...current.raw_metadata, zsxq_search: { ...current.raw_metadata.zsxq_search, ...next } },
    }));
  }

  return { form, setForm, catalog, catalogError, capability, sourceType,
    changePlatform, changeSourceType, updateZsxqSearch };
}
