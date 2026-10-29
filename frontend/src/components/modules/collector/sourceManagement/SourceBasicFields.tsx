import { Input } from "../../../ui/primitives/Input";
import { Select } from "../../../ui/primitives/Select";
import type { useSourceForm } from "./useSourceForm";

type Props = { state: ReturnType<typeof useSourceForm>; editing: boolean; busy: boolean };

export function SourceBasicFields({ state, editing, busy }: Props) {
  const { form, setForm, catalog, capability, sourceType, changePlatform, changeSourceType } = state;
  return (
        <div className="collector-source-form__grid">
          <Select
            label="平台"
            aria-label="平台"
            required
            disabled={editing || busy}
            value={form.platform}
            onChange={(event) => changePlatform(event.target.value)}
          >
            <option value="">请选择平台</option>
            {catalog.map((item) => (
              <option key={item.platform} value={item.platform}>
                {item.label}
              </option>
            ))}
          </Select>
          <Input
            label="来源名称"
            aria-label="来源名称"
            required
            disabled={busy}
            value={form.display_name}
            onChange={(event) => setForm({ ...form, display_name: event.target.value })}
          />
          <Select
            label="来源类型"
            aria-label="来源类型"
            required
            disabled={editing || busy}
            value={form.source_type}
            onChange={(event) => changeSourceType(event.target.value)}
          >
            {!capability?.source_types.some((item) => item.source_type === form.source_type) && form.source_type ? (
              <option value={form.source_type}>{form.source_type}</option>
            ) : null}
            {capability?.source_types.map((item) => (
              <option key={item.source_type} value={item.source_type}>
                {item.label}
              </option>
            ))}
          </Select>
          {sourceType?.identifier_mode === "select" ? (
            <Select
              label="来源地址或标识"
              aria-label="来源地址或标识"
              required
              disabled={editing || busy}
              hint={editing ? "稳定身份不可修改；如需变更请新建来源。" : sourceType.identifier_hint}
              value={form.identifier}
              onChange={(event) => setForm({ ...form, identifier: event.target.value })}
            >
              <option value="">请选择标识</option>
              {sourceType.identifier_options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          ) : (
            <Input
              label="来源地址或标识"
              aria-label="来源地址或标识"
              required
              disabled={editing || busy}
              hint={editing ? "稳定身份不可修改；如需变更请新建来源。" : sourceType?.identifier_hint ?? undefined}
              value={form.identifier}
              onChange={(event) => setForm({ ...form, identifier: event.target.value })}
            />
          )}
          <Select
            label="采集频率"
            aria-label="采集频率"
            required
            disabled={busy}
            value={String(form.cadence_hours)}
            onChange={(event) => setForm({ ...form, cadence_hours: Number(event.target.value) })}
          >
            {!capability || !capability.cadence_options.includes(form.cadence_hours) ? (
              <option value={form.cadence_hours}>每 {form.cadence_hours} 小时</option>
            ) : null}
            {capability?.cadence_options.map((hours) => (
              <option key={hours} value={hours}>
                每 {hours} 小时
              </option>
            ))}
          </Select>
          <Input
            label="单次最大条数"
            aria-label="单次最大条数"
            type="number"
            min={1}
            max={sourceType?.max_items_limit ?? capability?.max_items_limit ?? 1000}
            required
            disabled={busy}
            value={form.max_items_per_run}
            onChange={(event) => setForm({ ...form, max_items_per_run: Number(event.target.value) })}
          />
        </div>
  );
}
