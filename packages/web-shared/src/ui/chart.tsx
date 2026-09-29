/**
 * Stacked column chart (dataviz skill: thin marks ≤24px, 4px rounded data-end, 2px surface gap between
 * stacked segments, hairline recessive grid, legend for ≥2 series, hover tooltip, table view).
 * Series colors come from the validated palette tokens --series-1..3; text never uses series colors.
 */
import { BarChart3, Table2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "../lib/cn";
import { fmt } from "../lib/format";

export interface Series {
  key: string;
  label: string;
}

const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];

function niceMax(v: number): number {
  if (v <= 0) return 4;
  const exp = 10 ** Math.floor(Math.log10(v));
  const f = v / exp;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nice * exp;
}

function topRounded(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}

export function ColumnChart({
  data,
  xKey,
  series,
  formatX = (v) => v,
  height = 240,
  title,
}: {
  data: Record<string, number | string>[];
  xKey: string;
  series: Series[];
  formatX?: (v: string) => string;
  height?: number;
  title: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);
  const [view, setView] = useState<"chart" | "table">("chart");
  const s = series.slice(0, 3); // never more than 3 categorical slots (validated all-pairs set)

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const totals = useMemo(() => data.map((d) => s.reduce((acc, se) => acc + Number(d[se.key] || 0), 0)), [data, s]);
  const max = niceMax(Math.max(0, ...totals));
  const pad = { top: 12, right: 12, bottom: 28, left: 48 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const band = data.length ? innerW / data.length : innerW;
  const barW = Math.max(2, Math.min(24, band * 0.6));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const labelEvery = Math.max(1, Math.ceil(data.length / Math.max(1, Math.floor(innerW / 64))));
  const yFor = (v: number) => pad.top + innerH - (v / max) * innerH;

  return (
    <figure className="w-full" aria-label={title}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        {s.length > 1 ? (
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-secondary" aria-label="Legend">
            {s.map((se, i) => (
              <li key={se.key} className="flex items-center gap-1.5">
                <span className="inline-block size-2.5 rounded-sm" style={{ background: SERIES_COLORS[i] }} aria-hidden />
                {se.label}
              </li>
            ))}
          </ul>
        ) : (
          <span />
        )}
        <div className="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="View">
          {(["chart", "table"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              aria-pressed={view === v}
              className={cn(
                "flex items-center gap-1 rounded px-2 py-0.5 text-xs [&_svg]:size-3.5",
                view === v ? "bg-surface-2 text-fg" : "text-fg-muted hover:text-fg",
              )}
            >
              {v === "chart" ? <BarChart3 /> : <Table2 />}
              {v === "chart" ? "Chart" : "Table"}
            </button>
          ))}
        </div>
      </div>

      {view === "table" ? (
        <div className="max-h-72 overflow-auto rounded-lg border border-border">
          <table className="w-full text-sm tabular">
            <thead className="sticky top-0 bg-surface-2">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-fg-secondary">Period</th>
                {s.map((se) => (
                  <th key={se.key} className="px-3 py-2 text-right font-medium text-fg-secondary">
                    {se.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((d, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="px-3 py-1.5 text-fg">{formatX(String(d[xKey]))}</td>
                  {s.map((se) => (
                    <td key={se.key} className="px-3 py-1.5 text-right text-fg">
                      {fmt(Number(d[se.key] || 0))}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={wrap} className="relative w-full" style={{ height }}>
          {data.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-fg-muted">No data for this period</div>
          ) : (
            <svg width={width} height={height} role="img" aria-label={title} onMouseLeave={() => setHover(null)}>
              {ticks.map((t) => (
                <g key={t}>
                  <line x1={pad.left} x2={width - pad.right} y1={yFor(t)} y2={yFor(t)} stroke={t === 0 ? "var(--chart-axis)" : "var(--chart-grid)"} strokeWidth={1} />
                  <text x={pad.left - 8} y={yFor(t)} dy="0.32em" textAnchor="end" className="fill-[var(--fg-muted)] text-[11px] tabular">
                    {fmt(Math.round(t))}
                  </text>
                </g>
              ))}
              {data.map((d, i) => {
                const cx = pad.left + band * i + band / 2;
                let acc = 0;
                const segs = s.map((se, si) => {
                  const v = Number(d[se.key] || 0);
                  const y0 = yFor(acc);
                  acc += v;
                  const y1 = yFor(acc);
                  return { v, si, y: y1, h: y0 - y1 };
                });
                const visible = segs.filter((g) => g.v > 0);
                const top = visible[visible.length - 1];
                return (
                  <g key={i}>
                    {hover === i && <rect x={pad.left + band * i} y={pad.top} width={band} height={innerH} fill="var(--fg)" opacity={0.04} />}
                    {visible.map((g, gi) => {
                      // 2px surface gap between stacked segments (except at the baseline).
                      const gap = gi > 0 ? 2 : 0;
                      const h = Math.max(0, g.h - gap);
                      if (h <= 0) return null;
                      return g === top ? (
                        <path key={g.si} d={topRounded(cx - barW / 2, g.y, barW, h, 4)} fill={SERIES_COLORS[g.si]} />
                      ) : (
                        <rect key={g.si} x={cx - barW / 2} y={g.y} width={barW} height={h} fill={SERIES_COLORS[g.si]} />
                      );
                    })}
                    {i % labelEvery === 0 && (
                      <text x={cx} y={height - 8} textAnchor="middle" className="fill-[var(--fg-muted)] text-[11px]">
                        {formatX(String(d[xKey]))}
                      </text>
                    )}
                    {/* hit target larger than the mark */}
                    <rect
                      x={pad.left + band * i}
                      y={pad.top}
                      width={band}
                      height={innerH}
                      fill="transparent"
                      onMouseEnter={() => setHover(i)}
                      onFocus={() => setHover(i)}
                      tabIndex={-1}
                    />
                  </g>
                );
              })}
            </svg>
          )}
          {hover != null && data[hover] && (
            <div
              className="pointer-events-none absolute z-10 min-w-36 rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-pop"
              style={{
                left: Math.min(width - 160, Math.max(0, pad.left + band * hover + band / 2 + 12)),
                top: pad.top,
              }}
            >
              <div className="mb-1 font-medium text-fg">{formatX(String(data[hover][xKey]))}</div>
              {s.map((se, i) => (
                <div key={se.key} className="flex items-center justify-between gap-4">
                  <span className="flex items-center gap-1.5 text-fg-secondary">
                    <span className="inline-block size-2 rounded-sm" style={{ background: SERIES_COLORS[i] }} />
                    {se.label}
                  </span>
                  <span className="tabular text-fg">{fmt(Number(data[hover][se.key] || 0))}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}
