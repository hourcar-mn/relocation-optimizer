/**
 * The cron fires every hour; this decides which of those ticks actually runs
 * the optimizer:
 *
 *   08:00 – 17:00 Central   every hour
 *   otherwise               every 3 hours, at 20:00, 23:00, 02:00 and 05:00
 *
 * Cron triggers are UTC-only, so the schedule is expressed in Central here
 * instead — which also means it follows DST instead of drifting an hour twice
 * a year.
 */
export function shouldRun(at: Date): boolean {
  const hour = Number(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Chicago",
      hour: "numeric",
      hourCycle: "h23",
    }).format(at),
  );

  if (hour >= 8 && hour <= 17) return true;
  // off hours: every third hour, continuing the rhythm from 17:00
  return hour % 3 === 2;
}
