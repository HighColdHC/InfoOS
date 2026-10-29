type DateValue = string | number | null;

export function formatCardPreviewDate(value: DateValue): string {
  if (value == null || value === "") return "未记录";
  const date = parseCardPreviewDate(value);
  if (!date) return String(value);
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function cardPreviewDateTime(value: DateValue): string | undefined {
  if (value == null || value === "") return undefined;
  return parseCardPreviewDate(value)?.toISOString();
}

function parseCardPreviewDate(value: Exclude<DateValue, null>): Date | null {
  const numeric = typeof value === "number"
    ? value
    : /^\d+(?:\.\d+)?$/.test(value.trim())
      ? Number(value)
      : null;
  const date = numeric == null
    ? new Date(value)
    : new Date(numeric < 1_000_000_000_000 ? numeric * 1_000 : numeric);
  return Number.isNaN(date.getTime()) ? null : date;
}
