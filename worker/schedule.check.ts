// Checks the cron gate, including both sides of DST. Run: node worker/schedule.check.ts
import assert from "node:assert";
import { shouldRun } from "./schedule.ts";

// summer (CDT, UTC-5) and winter (CST, UTC-6) — same Central wall clock times
const cases: [string, string, boolean][] = [
  // [description, UTC instant, should the optimizer run]
  ["05:30 Central, off hours", "2026-08-11T10:30:00Z", false],
  ["06:00 Central, window opens", "2026-08-11T11:00:00Z", true],
  ["12:30 Central, midday", "2026-08-11T17:30:00Z", true],
  ["18:30 Central, last frequent run", "2026-08-11T23:30:00Z", true],
  ["19:00 Central, first off-hours run", "2026-08-12T00:00:00Z", true],
  ["19:30 Central, skipped", "2026-08-12T00:30:00Z", false],
  ["22:00 Central, every third hour", "2026-08-12T03:00:00Z", true],
  ["23:00 Central, skipped", "2026-08-12T04:00:00Z", false],
  ["01:00 Central, every third hour", "2026-08-12T06:00:00Z", true],
  ["04:00 Central, every third hour", "2026-08-12T09:00:00Z", true],
  // winter: the same Central times land an hour later in UTC
  ["06:00 Central in CST", "2026-01-15T12:00:00Z", true],
  ["05:30 Central in CST", "2026-01-15T11:30:00Z", false],
  ["19:00 Central in CST", "2026-01-16T01:00:00Z", true],
];

for (const [label, iso, expected] of cases) {
  assert.strictEqual(shouldRun(new Date(iso)), expected, `${label} (${iso})`);
}

// off hours should fire exactly every 3 hours: 19, 22, 01, 04
const firing: number[] = [];
for (let i = 0; i < 48; i++) {
  const at = new Date(Date.parse("2026-08-11T11:00:00Z") + i * 30 * 60 * 1000);
  if (shouldRun(at)) firing.push(i);
}
// 06:00–18:30 is 26 ticks, plus 19:00, 22:00, 01:00, 04:00
assert.strictEqual(firing.length, 30, `expected 30 runs per day, got ${firing.length}`);

console.log("ok");
