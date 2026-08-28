"use client";

/**
 * Legend rendered as HTML rather than recharts' SVG default.
 *
 * Two or more series always get one, so identity never rests on colour alone.
 * The label wears text ink with a colour swatch beside it, rather than being
 * coloured itself.
 */
export default function ChartLegend({items}: {items: Array<{name: string; color: string; dashed?: boolean}>}) {
  return (
    <div className="chartLegend">
      {items.map((item) => (
        <span className="chartLegendItem" key={item.name}>
          <span
            className="chartLegendSwatch"
            style={item.dashed
              ? {background: `repeating-linear-gradient(90deg, ${item.color} 0 4px, transparent 4px 7px)`}
              : {background: item.color}}
            aria-hidden
          />
          {item.name}
        </span>
      ))}
    </div>
  );
}
