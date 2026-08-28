/**
 * Inline 16px icons.
 *
 * Hand-written paths rather than an icon dependency, and rather than emoji: emoji
 * render differently on every platform, cannot inherit colour, and were the main
 * thing making the calendar look improvised.
 */
const PATHS: Record<string, string> = {
  dashboard: "M2 2h5v5H2zM9 2h5v5H9zM2 9h5v5H2zM9 9h5v5H9z",
  calendar: "M3 2v1H2.5A1.5 1.5 0 0 0 1 4.5v9A1.5 1.5 0 0 0 2.5 15h11a1.5 1.5 0 0 0 1.5-1.5v-9A1.5 1.5 0 0 0 13.5 3H13V2h-1v1H4V2zM2 6h12v7.5a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5z",
  season: "M3 1v14H2V1zM4 2h8.5l-1.6 3L12.5 8H4z",
  activities: "M1 3h14v1.4H1zM1 7.3h14v1.4H1zM1 11.6h14v1.4H1z",
  power: "M9.5 1 4 9h3.2l-.7 6 5.5-8H8.8z",
  thresholds: "M8 2a6 6 0 0 0-6 6h1.6A4.4 4.4 0 0 1 8 3.6 4.4 4.4 0 0 1 12.4 8H14a6 6 0 0 0-6-6m0 3.4A2.6 2.6 0 0 0 5.4 8H7a1 1 0 0 1 2 0h1.6A2.6 2.6 0 0 0 8 5.4M2 9.4h12V11H2z",
  imports: "M7.25 1v7.1L4.9 5.75l-1 1L8 10.85l4.1-4.1-1-1L8.75 8.1V1zM2 12.5h12V14H2z",
  coach: "M2.5 2h11A1.5 1.5 0 0 1 15 3.5v7A1.5 1.5 0 0 1 13.5 12H7l-4 3v-3H2.5A1.5 1.5 0 0 1 1 10.5v-7A1.5 1.5 0 0 1 2.5 2",
  lock: "M8 1a3 3 0 0 0-3 3v2H4.5A1.5 1.5 0 0 0 3 7.5v6A1.5 1.5 0 0 0 4.5 15h7a1.5 1.5 0 0 0 1.5-1.5v-6A1.5 1.5 0 0 0 11.5 6H11V4a3 3 0 0 0-3-3m0 1.5A1.5 1.5 0 0 1 9.5 4v2h-3V4A1.5 1.5 0 0 1 8 2.5",
  drag: "M6 3.5h1.5V5H6zM8.5 3.5H10V5H8.5zM6 7.25h1.5v1.5H6zM8.5 7.25H10v1.5H8.5zM6 11h1.5v1.5H6zM8.5 11H10v1.5H8.5z",
};

export default function Icon({name, size = 16}: {name: keyof typeof PATHS | string; size?: number}) {
  const path = PATHS[name];
  if (!path) return null;
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="currentColor" aria-hidden focusable="false" className="icon">
      <path d={path} />
    </svg>
  );
}
