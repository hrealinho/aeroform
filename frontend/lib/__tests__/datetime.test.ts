import {describe, expect, it} from "vitest";
import {formatChartDate, formatChartDateLong, formatDuration, formatDurationHours, formatMonthTick,
        localDateTimeToUTC, localISODate, localISODateOf, localTime, middayLocal, monthTicks} from "../datetime";

// These helpers exist because `toISOString().slice(0,10)` returns the UTC date, which is
// the wrong calendar day for most of the world for part of every day.
describe("localISODate", () => {
  it("uses the local calendar day, not the UTC one", () => {
    // 23:30 local on the 1st. In any timezone ahead of UTC this is already the 2nd in UTC.
    const late = new Date(2026, 8, 1, 23, 30, 0);
    expect(localISODate(late)).toBe("2026-09-01");
  });

  it("pads single-digit months and days", () => {
    expect(localISODate(new Date(2026, 0, 5, 12, 0, 0))).toBe("2026-01-05");
  });

  it("round-trips every hour of a day to the same calendar date", () => {
    for (let hour = 0; hour < 24; hour++) {
      expect(localISODate(new Date(2026, 5, 15, hour, 30, 0))).toBe("2026-06-15");
    }
  });
});

describe("localDateTimeToUTC", () => {
  it("produces an absolute instant that renders back as the entered local time", () => {
    const iso = localDateTimeToUTC("2026-09-01", "18:00");
    const back = new Date(iso);
    expect(back.getFullYear()).toBe(2026);
    expect(back.getMonth()).toBe(8);
    expect(back.getDate()).toBe(1);
    expect(localTime(back)).toBe("18:00");
  });

  it("agrees with the convention the drag-to-move path uses", () => {
    // Regression: create sent a naive local string while move sent toISOString(), so
    // dragging a workout shifted its clock time by the user's UTC offset.
    const created = localDateTimeToUTC("2026-09-01", "18:00");
    const moved = new Date(2026, 8, 1, 18, 0, 0, 0).toISOString();
    expect(created).toBe(moved);
  });

  it("keeps the calendar day stable when grouping an instant back into a column", () => {
    const iso = localDateTimeToUTC("2026-09-01", "00:30");
    expect(localISODateOf(iso)).toBe("2026-09-01");
  });

  it("handles a late-evening entry without rolling to the next day", () => {
    const iso = localDateTimeToUTC("2026-09-01", "23:45");
    expect(localISODateOf(iso)).toBe("2026-09-01");
  });
});

describe("middayLocal", () => {
  it("is safe to shift by whole days across a DST boundary", () => {
    const start = middayLocal("2026-03-28");
    const next = new Date(start);
    next.setDate(next.getDate() + 2);
    expect(localISODate(next)).toBe("2026-03-30");
  });
});

describe("formatDuration", () => {
  it("shows hours and minutes, not a decimal fraction", () => {
    // The values from the Activities table screenshot.
    expect(formatDuration(1.3 * 3600)).toBe("1:18 h");
    expect(formatDuration(3.4 * 3600)).toBe("3:24 h");
    expect(formatDuration(0.8 * 3600)).toBe("48 min");
  });

  it("pads minutes so the column stays aligned", () => {
    expect(formatDuration(3600 + 5 * 60)).toBe("1:05 h");
    expect(formatDuration(2 * 3600)).toBe("2:00 h");
  });

  it("uses plain minutes below an hour", () => {
    expect(formatDuration(45 * 60)).toBe("45 min");
    expect(formatDuration(90)).toBe("2 min");
  });

  it("handles long mountain days", () => {
    expect(formatDuration(12.75 * 3600)).toBe("12:45 h");
  });

  it("does not invent a duration from missing data", () => {
    expect(formatDuration(null)).toBe("-");
    expect(formatDuration(undefined)).toBe("-");
    expect(formatDuration(0)).toBe("-");
    expect(formatDuration(NaN)).toBe("-");
  });

  it("round-trips a decimal-hours value from the API", () => {
    expect(formatDurationHours(1.3)).toBe("1:18 h");
    expect(formatDurationHours(null)).toBe("-");
  });
});

describe("chart date formatting", () => {
  it("parses a calendar date locally, so the label is never a day out", () => {
    // new Date("2026-09-02") is UTC midnight: behind UTC that formats as 1 September.
    expect(formatChartDate("2026-09-02")).toBe(
      middayLocal("2026-09-02").toLocaleDateString(undefined, {day: "numeric", month: "short"}),
    );
    expect(formatChartDateLong("2026-09-02")).toContain("2026");
  });

  it("states the year only where it turns", () => {
    expect(formatMonthTick("2026-09-01")).not.toMatch(/2026/);
    expect(formatMonthTick("2027-01-01")).toMatch(/2027/);
  });

  it("puts ticks on month boundaries", () => {
    const rows = [];
    for (let m = 1; m <= 6; m++) {
      for (let d = 1; d <= 28; d++) {
        rows.push({date: `2026-0${m}-${`${d}`.padStart(2, "0")}`});
      }
    }
    expect(monthTicks(rows)).toEqual([
      "2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01",
    ]);
  });

  it("thins ticks when a range spans too many months", () => {
    const rows = Array.from({length: 36}, (_, i) => ({
      date: `${2024 + Math.floor(i / 12)}-${`${(i % 12) + 1}`.padStart(2, "0")}-01`,
    }));
    const ticks = monthTicks(rows, "date", 8);
    expect(ticks.length).toBeLessThanOrEqual(8);
    expect(ticks[0]).toBe("2024-01-01");
  });

  it("tolerates empty and malformed rows", () => {
    expect(monthTicks([])).toEqual([]);
    expect(monthTicks([{date: null}, {date: "x"}, {date: "2026-05-04"}] as any)).toEqual(["2026-05-04"]);
    expect(formatChartDate("")).toBe("");
    expect(formatMonthTick("")).toBe("");
  });
});
