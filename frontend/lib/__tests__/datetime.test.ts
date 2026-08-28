import {describe, expect, it} from "vitest";
import {localDateTimeToUTC, localISODate, localISODateOf, localTime, middayLocal} from "../datetime";

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
