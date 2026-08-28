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
