// Checks the cron gate, including both sides of DST. Run: node worker/schedule.check.ts
import assert from "node:assert";
import { shouldRun } from "./schedule.ts";

// summer (CDT, UTC-5) and winter (CST, UTC-6) — same Central wall clock times
const cases: [string, string, boolean][] = [
  // [description, UTC instant, should the optimizer run]
  ["07:00 Central, off hours", "2026-08-11T12:00:00Z", false],
  ["08:00 Central, workday opens", "2026-08-11T13:00:00Z", true],
  ["12:00 Central, midday", "2026-08-11T17:00:00Z", true],
  ["17:00 Central, last hourly run", "2026-08-11T22:00:00Z", true],
  ["18:00 Central, skipped", "2026-08-11T23:00:00Z", false],
  ["20:00 Central, first off-hours run", "2026-08-12T01:00:00Z", true],
  ["21:00 Central, skipped", "2026-08-12T02:00:00Z", false],
  ["23:00 Central, every third hour", "2026-08-12T04:00:00Z", true],
  ["02:00 Central, every third hour", "2026-08-12T07:00:00Z", true],
  ["05:00 Central, every third hour", "2026-08-12T10:00:00Z", true],
  // winter: the same Central times land an hour later in UTC
  ["08:00 Central in CST", "2026-01-15T14:00:00Z", true],
  ["07:00 Central in CST", "2026-01-15T13:00:00Z", false],
  ["20:00 Central in CST", "2026-01-16T02:00:00Z", true],
];

for (const [label, iso, expected] of cases) {
  assert.strictEqual(shouldRun(new Date(iso)), expected, `${label} (${iso})`);
}

// one day of hourly ticks: 08:00–17:00 is 10 runs, plus 20:00, 23:00, 02:00, 05:00
let runs = 0;
for (let i = 0; i < 24; i++) {
  if (shouldRun(new Date(Date.parse("2026-08-11T13:00:00Z") + i * 60 * 60 * 1000))) runs++;
}
assert.strictEqual(runs, 14, `expected 14 runs per day, got ${runs}`);

console.log("ok");
