import type { useSourceForm } from "./useSourceForm";

type Props = { state: ReturnType<typeof useSourceForm>; busy: boolean };

export function SourceMediaAiFields({ state, busy }: Props) {
  const { form, setForm } = state;
  return (
    <>
        <div className="collector-source-form__checks">
          <label>
            <input
              type="checkbox"
              checked={form.enabled}
              disabled={busy}
              onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
            />
            启用定时采集
          </label>
          <label>
            <input
              type="checkbox"
              checked={form.raw_metadata.auto_enrich_media}
              disabled={busy}
              onChange={(event) =>
                setForm({ ...form, raw_metadata: { ...form.raw_metadata, auto_enrich_media: event.target.checked } })
              }
            />
            自动补全媒体
          </label>
        </div>
    </>
  );
}
