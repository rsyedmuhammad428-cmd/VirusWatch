/**
 * Consistent date, time, number and unit formatting across the app.
 * Missing values render as an explicit em dash, never as zero.
 */

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatNumber(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function formatValueWithUnit(value?: number | null, unit?: string | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return unit ? `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}` : formatNumber(value);
}

export function formatPeriod(start?: string | null, end?: string | null): string {
  if (start && end) {
    if (start === end) return formatDate(start);
    return `${formatDate(start)} – ${formatDate(end)}`;
  }
  if (start) return `from ${formatDate(start)}`;
  if (end) return `until ${formatDate(end)}`;
  return "—";
}

/** Human label for geographic levels, shown wherever a value is displayed. */
export function geographicLevelLabel(level?: string | null): string {
  switch (level) {
    case "city":
      return "City-level data";
    case "province":
      return "Provincial / state-level data";
    case "national":
      return "National data";
    case "other":
      return "Other geographic unit";
    default:
      return level ? level : "Geographic level unknown";
  }
}

export function relativeTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffMs = Date.now() - date.getTime();
  const minutes = Math.round(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;
  return formatDate(value);
}
