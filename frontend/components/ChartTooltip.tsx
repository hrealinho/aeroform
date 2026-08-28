"use client";
import {formatChartDateLong} from "@/lib/datetime";

type Entry = {dataKey?: string | number; name?: string; value?: number | string; color?: string};
type Props = {active?: boolean; payload?: Entry[]; label?: string | number};

/**
 * Tooltip matching the app's dark surface.
 *
 * The recharts default is a white box, so on this theme its date heading rendered as
 * light grey on white and was effectively invisible. Surface, border and ink come from
 * the same tokens `.card` uses in globals.css. Series names stay in text colour with a
 * colour chip beside them, so identity is never carried by coloured text alone.
 */
export default function ChartTooltip({active, payload, label}: Props) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chartTooltip">
      <div className="chartTooltipDate">{formatChartDateLong(String(label ?? ""))}</div>
      {payload.map((entry, index) => (
        <div className="chartTooltipRow" key={String(entry.dataKey ?? index)}>
          <span className="chartTooltipChip" style={{background: entry.color}} aria-hidden />
          <span className="chartTooltipName">{entry.name}</span>
          <span className="chartTooltipValue">
            {typeof entry.value === "number"
              ? entry.value.toLocaleString(undefined, {maximumFractionDigits: 1})
              : entry.value}
          </span>
        </div>
      ))}
    </div>
  );
}
