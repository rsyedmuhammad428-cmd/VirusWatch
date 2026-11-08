import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { formatDateTime } from "@/lib/format";

/** Small pill showing surveillance-data cadence and freshness. */
export function FreshnessBadge({
  cadence,
  lastRetrieval,
  freshness,
  className,
}: {
  cadence?: string | null | undefined;
  lastRetrieval?: string | null | undefined;
  freshness?: string | null | undefined;
  className?: string;
}) {
  const label =
    freshness ??
    (cadence
      ? `${cadence} data`
      : lastRetrieval
        ? `Retrieved ${formatDateTime(lastRetrieval)}`
        : "Freshness unknown");
  const tone =
    freshness === "fresh"
      ? "bg-positive/15 text-positive-foreground border-positive/30"
      : freshness === "stale" || freshness === "delayed"
        ? "bg-warning/20 text-warning-foreground border-warning/40"
        : "bg-muted text-muted-foreground border-border";

  return (
    <Badge
      variant="outline"
      className={cn("gap-1.5 border font-medium", tone, className)}
      title={
        cadence
          ? `${cadence} reporting cadence${lastRetrieval ? ` — last retrieved ${formatDateTime(lastRetrieval)}` : ""}`
          : undefined
      }
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          freshness === "fresh" ? "bg-positive" : freshness === "stale" || freshness === "delayed" ? "bg-warning" : "bg-muted-foreground/50",
        )}
      />
      {label}
    </Badge>
  );
}
