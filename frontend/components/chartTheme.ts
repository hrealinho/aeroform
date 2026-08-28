/**
 * Chart colour roles, read from the CSS tokens in globals.css.
 *
 * Recharts needs concrete stroke/fill values rather than `var(--x)` in every
 * position, so the tokens are resolved once here. Nothing in a chart component
 * hard-codes a hex value, and no colour is chosen by eye: the categorical slots
 * were validated against this theme's surface for colourblind separation,
 * normal-vision separation and 3:1 contrast.
 */
const FALLBACK: Record<string, string> = {
  "--series-1": "#3987e5",
  "--series-2": "#d95926",
  "--series-3": "#199e70",
  "--series-4": "#c98500",
  "--series-5": "#d55181",
  "--series-fitness": "#3987e5",
  "--series-fatigue": "#d55181",
  "--series-form": "#c98500",
  "--series-reference": "#7d89b0",
  "--grid": "rgba(152,164,199,0.14)",
  "--ink-3": "#98a4c7",
  "--surface-2": "#141b31",
};

function token(name: string): string {
  if (typeof window === "undefined") return FALLBACK[name];
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || FALLBACK[name];
}

export const chart = {
  get fitness() { return token("--series-fitness"); },
  get fatigue() { return token("--series-fatigue"); },
  get form() { return token("--series-form"); },
  get reference() { return token("--series-reference"); },
  get grid() { return token("--grid"); },
  get axis() { return token("--ink-3"); },
  get surface() { return token("--surface-2"); },
  series(index: number) { return token(`--series-${(index % 5) + 1}`); },
};

/** Shared axis/grid props so every chart is laid out identically. */
export const axisProps = {
  tickMargin: 8,
  minTickGap: 16,
  stroke: FALLBACK["--ink-3"],
  tick: {fill: FALLBACK["--ink-3"], fontSize: 11},
} as const;
