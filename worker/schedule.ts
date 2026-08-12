/**
 * The cron fires every 30 minutes; this decides which of those ticks actually
 * runs the optimizer:
 *
 *   06:00 – 18:30 Central   every 30 minutes
 *   otherwise               every 3 hours, at 19:00, 22:00, 01:00 and 04:00
 *
 * Cron triggers are UTC-only, so the schedule is expressed in Central here
 * instead — which also means it follows DST instead of drifting an hour twice
 * a year.
 */
export function shouldRun(at: Date): boolean {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Chicago",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
  }).formatToParts(at);
  const hour = Number(parts.find((p) => p.type === "hour")!.value);
  const minute = Number(parts.find((p) => p.type === "minute")!.value);

  if (hour >= 6 && hour < 19) return true;
  // off hours: only the tick at the top of every third hour
  return hour % 3 === 1 && minute < 30;
}
