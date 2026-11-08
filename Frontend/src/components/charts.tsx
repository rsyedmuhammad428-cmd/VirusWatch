import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TimeSeriesPoint } from "@/lib/api/types";
import { formatNumber } from "@/lib/format";

/**
 * Surveillance time-series chart. Missing observations are rendered as gaps
 * (connectNulls=false) — never converted to zero.
 */
export function SurveillanceLineChart({
  data,
  seriesLabel,
  height = 260,
}: {
  data: TimeSeriesPoint[];
  seriesLabel: string;
  height?: number;
}) {
  const unit = data.find((d) => d.value !== null)?.unit ?? "";
  const tooltipUnit = unit ? ` ${unit}` : "";

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
          <XAxis
            dataKey="period"
            tick={{ fontSize: 11, fill: "var(--color-muted-foreground)" }}
            tickLine={false}
            axisLine={{ stroke: "var(--color-border)" }}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "var(--color-muted-foreground)" }}
            tickLine={false}
            axisLine={false}
            width={56}
          />
          <Tooltip
            contentStyle={{
              background: "var(--color-popover)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--color-foreground)", fontWeight: 600 }}
            formatter={(value) => {
              const v = value as number | string | null | undefined;
              if (v === null || v === undefined || v === "") {
                return ["No observation", seriesLabel];
              }
              return [`${formatNumber(Number(v))}${tooltipUnit}`, seriesLabel];
            }}
          />
          <Line
            type="monotone"
            dataKey="value"
            name={seriesLabel}
            stroke="var(--color-chart-1)"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
