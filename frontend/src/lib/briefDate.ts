/** Brief dates are Shanghai calendar days; a day's brief freezes at 07:00. */
export function shanghaiDate(offsetDays = 0, now = new Date()): string {
  const shifted = new Date(now.getTime() + 8 * 3_600_000 + offsetDays * 86_400_000);
  return shifted.toISOString().slice(0, 10);
}

export function latestBriefDate(now = new Date()): string {
  const shanghaiHour = (now.getUTCHours() + 8) % 24;
  return shanghaiDate(shanghaiHour >= 7 ? 0 : -1, now);
}

export function shiftDate(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(value.getTime())) return date;
  return new Date(value.getTime() + days * 86_400_000).toISOString().slice(0, 10);
}
