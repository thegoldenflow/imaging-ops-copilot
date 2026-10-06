// Small dependency-free SVG charts for dashboards.

import { useState } from "react";

const SERIES_COLORS = ["#0f6e72", "#d97706", "#6941c6", "#0284c7", "#e11d48", "#64748b"];

export function seriesColor(i: number) {
  return SERIES_COLORS[i % SERIES_COLORS.length];
}

/** Vertical bars with an optional dashed reference line (e.g. a target). */
export function BarChart({ data, height = 140, reference, referenceLabel, format = (v) => String(v), label }: {
  data: { label: string; value: number | null; tone?: "red" | "amber" }[];
  height?: number;
  reference?: number;
  referenceLabel?: string;
  format?: (v: number) => string;
  label: string;
}) {
  const max = Math.max(reference ?? 0, ...data.map((d) => d.value ?? 0)) * 1.15 || 1;
  const width = 600;
  const barW = width / data.length;
  const y = (v: number) => height - 18 - (v / max) * (height - 30);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={label}>
      {data.map((d, i) => (
        <g key={d.label}>
          {d.value != null && (
            <>
              <rect x={i * barW + barW * 0.18} y={y(d.value)} width={barW * 0.64} height={height - 18 - y(d.value)} rx={3}
                fill={d.tone === "red" ? "#e11d48" : d.tone === "amber" ? "#d97706" : "#14868a"} opacity={0.85}>
                <title>{`${d.label}: ${format(d.value)}`}</title>
              </rect>
              <text x={i * barW + barW / 2} y={y(d.value) - 4} textAnchor="middle" className="fill-slate-600 text-[11px]">{format(d.value)}</text>
            </>
          )}
          <text x={i * barW + barW / 2} y={height - 4} textAnchor="middle" className="fill-slate-500 text-[11px]">{d.label}</text>
        </g>
      ))}
      {reference != null && (
        <g>
          <line x1={0} x2={width} y1={y(reference)} y2={y(reference)} stroke="#e11d48" strokeDasharray="4 4" strokeWidth={1} />
          {referenceLabel && <text x={width - 4} y={y(reference) - 4} textAnchor="end" className="fill-rose-600 text-[11px]">{referenceLabel}</text>}
        </g>
      )}
    </svg>
  );
}

/** Multi-series line chart over shared x labels, with an optional reference line and a hover readout. */
export function LineChart({ labels, series, height = 200, reference, referenceLabel, format = (v) => String(v), label }: {
  labels: string[];
  series: { name: string; values: (number | null)[] }[];
  height?: number;
  reference?: number;
  referenceLabel?: string;
  format?: (v: number) => string;
  label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 640;
  const left = 40;
  const values = series.flatMap((s) => s.values.filter((v): v is number => v != null));
  const max = Math.max(reference ?? 0, ...values) * 1.1 || 1;
  const step = labels.length > 1 ? (width - left - 10) / (labels.length - 1) : 0;
  const x = (i: number) => left + i * step;
  const y = (v: number) => height - 22 - (v / max) * (height - 34);
  const ticks = [0, max / 2, max];
  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={label} onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={left} x2={width - 10} y1={y(t)} y2={y(t)} stroke="#e2e8f0" />
            <text x={left - 6} y={y(t) + 4} textAnchor="end" className="fill-slate-400 text-[10px]">{format(t)}</text>
          </g>
        ))}
        {labels.map((l, i) => (i % Math.ceil(labels.length / 8) === 0 || i === labels.length - 1) && (
          <text key={l} x={x(i)} y={height - 6} textAnchor={i === labels.length - 1 ? "end" : "middle"} className="fill-slate-500 text-[10px]">{l}</text>
        ))}
        {reference != null && (
          <g>
            <line x1={left} x2={width - 10} y1={y(reference)} y2={y(reference)} stroke="#e11d48" strokeDasharray="4 4" />
            {referenceLabel && <text x={width - 12} y={y(reference) - 4} textAnchor="end" className="fill-rose-600 text-[10px]">{referenceLabel}</text>}
          </g>
        )}
        {series.map((s, si) => {
          const pts = s.values.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean);
          return (
            <g key={s.name}>
              <polyline points={pts.join(" ")} fill="none" stroke={seriesColor(si)} strokeWidth={2} />
              {s.values.map((v, i) => v != null && <circle key={i} cx={x(i)} cy={y(v)} r={hover === i ? 4 : 2.5} fill={seriesColor(si)} />)}
            </g>
          );
        })}
        {labels.map((l, i) => (
          <rect key={l} x={x(i) - step / 2} y={0} width={Math.max(step, 8)} height={height} fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
        {hover != null && <line x1={x(hover)} x2={x(hover)} y1={6} y2={height - 22} stroke="#94a3b8" strokeDasharray="2 3" />}
      </svg>
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
        {hover != null && <span className="font-medium text-slate-800">{labels[hover]}</span>}
        {series.map((s, si) => (
          <span key={s.name} className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-3 rounded-sm" style={{ background: seriesColor(si) }} />
            {s.name}
            {hover != null && s.values[hover] != null && <span className="tabular font-medium">{format(s.values[hover]!)}</span>}
          </span>
        ))}
      </div>
    </div>
  );
}
