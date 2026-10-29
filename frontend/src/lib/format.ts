export function formatLocalDateTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
}

// ---------------------------------------------------------------------------
// Class-name / string formatting helpers
// ---------------------------------------------------------------------------

export type ClassValue =
  | string
  | false
  | null
  | undefined
  | Record<string, boolean | null | undefined>;

/** Concatenate className fragments, dropping falsy values; object args spread by truthy keys. */
export function cx(...args: ClassValue[]): string {
  const out: string[] = [];
  for (const arg of args) {
    if (!arg) continue;
    if (typeof arg === "string") {
      if (arg.length > 0) out.push(arg);
    } else if (typeof arg === "object") {
      for (const key of Object.keys(arg)) {
        if (arg[key]) out.push(key);
      }
    }
  }
  return out.join(" ");
}

/** Truncate `text` to at most `maxLen` characters, appending `suffix` when cut. */
export function truncate(text: string, maxLen: number, suffix = "…"): string {
  return text.length <= maxLen ? text : text.slice(0, maxLen) + suffix;
}

/** Format an ISO string / Date as `YYYY-MM-DD HH:mm` in local time; null/undefined/invalid → "". */
export function formatCompactDate(iso: string | Date | null | undefined): string {
  if (iso == null) return "";
  const date = iso instanceof Date ? iso : new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())} ${p(date.getHours())}:${p(date.getMinutes())}`;
}

/** Extract a human-readable message from an unknown caught error. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return String(err);
}
