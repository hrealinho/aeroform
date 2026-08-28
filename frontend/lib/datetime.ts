// Date helpers that respect the user's local calendar.
//
// `new Date().toISOString().slice(0,10)` returns the UTC date, which is the previous or
// next day for anyone not on UTC once it is late enough locally. Every calendar column,
// default form value and day-grouping key here is built from local components instead.

/** Local calendar date as YYYY-MM-DD. */
export function localISODate(d: Date): string {
  const year = d.getFullYear();
  const month = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Local calendar date of an instant returned by the API. */
export function localISODateOf(instant: string): string {
  return localISODate(new Date(instant));
}

/** Local HH:MM. */
export function localTime(d: Date): string {
  return `${`${d.getHours()}`.padStart(2, "0")}:${`${d.getMinutes()}`.padStart(2, "0")}`;
}

/**
 * Absolute UTC instant for a local date + time entered by the user.
 *
 * The API stores UTC, so both creating and moving a workout must send an absolute
 * instant. Sending a naive local string from one path and `toISOString()` from the
 * other made a dragged session shift by the user's UTC offset.
 */
export function localDateTimeToUTC(date: string, time: string): string {
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute] = time.split(":").map(Number);
  return new Date(year, (month || 1) - 1, day || 1, hour || 0, minute || 0, 0, 0).toISOString();
}

/** Local midday for a calendar date, safe to shift by whole days across DST. */
export function middayLocal(date: string): Date {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(year, (month || 1) - 1, day || 1, 12, 0, 0, 0);
}

// ---------------------------------------------------------------------------
// Durations
// ---------------------------------------------------------------------------

/**
 * Duration as hours and minutes, e.g. "1:18 h".
 *
 * Decimal hours ("1.3 h") force the reader to convert a fraction back into minutes,
 * and rounding to one decimal loses up to three minutes. Training durations are read
 * as clock time, so they are shown that way.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "-";
  const total = Math.round(seconds / 60);
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  // Under an hour reads better in plain minutes than as "0:45 h".
  if (hours === 0) return `${minutes} min`;
  return `${hours}:${`${minutes}`.padStart(2, "0")} h`;
}

/** Same, from a decimal-hours value supplied by the API. */
export function formatDurationHours(hours: number | null | undefined): string {
  if (hours == null || !Number.isFinite(hours)) return "-";
  return formatDuration(hours * 3600);
}

// ---------------------------------------------------------------------------
// Chart axis and tooltip formatting
//
// Series data keys are plain calendar dates ("2026-09-02"), not instants. They must be
// parsed as local calendar dates: `new Date("2026-09-02")` is UTC midnight, so anywhere
// behind UTC it formats as September 1st and every axis label is off by a day.
// ---------------------------------------------------------------------------

/** Compact axis tick, e.g. "2 Sep". Includes the year only when asked. */
export function formatChartDate(iso: string, withYear = false): string {
  if (!iso) return "";
  return middayLocal(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(withYear ? {year: "numeric"} : {}),
  });
}

/** Month-boundary tick, e.g. "Sep", or "Jan 2027" where the year turns. */
export function formatMonthTick(iso: string): string {
  if (!iso) return "";
  const d = middayLocal(iso);
  // State the year only where it changes, so it appears once rather than on every tick.
  const showYear = d.getMonth() === 0;
  return d.toLocaleDateString(undefined, {month: "short", ...(showYear ? {year: "numeric"} : {})});
}

/** Unambiguous tooltip heading, e.g. "Wed 2 Sep 2026". */
export function formatChartDateLong(iso: string): string {
  if (!iso) return "";
  return middayLocal(iso).toLocaleDateString(undefined, {
    weekday: "short", day: "numeric", month: "short", year: "numeric",
  });
}

/**
 * Tick values landing on month boundaries.
 *
 * Recharts otherwise spaces ticks by pixel gap, which lands labels on arbitrary days
 * ("2026-09-02", "2026-10-08") and makes a multi-month axis hard to scan. Taking the
 * first row of each month gives evenly spaced, meaningful ticks; `maxTicks` thins them
 * when the range is long enough that every month would collide.
 */
export function monthTicks(rows: Array<Record<string, unknown>>, key = "date", maxTicks = 8): string[] {
  const firstOfMonth: string[] = [];
  let seen = "";
  for (const row of rows ?? []) {
    const value = row?.[key];
    if (typeof value !== "string" || value.length < 7) continue;
    const month = value.slice(0, 7);
    if (month !== seen) {
      seen = month;
      firstOfMonth.push(value);
    }
  }
  if (firstOfMonth.length <= maxTicks) return firstOfMonth;
  const stride = Math.ceil(firstOfMonth.length / maxTicks);
  return firstOfMonth.filter((_, i) => i % stride === 0);
}
