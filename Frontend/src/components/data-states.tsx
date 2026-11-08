import { Link } from "@tanstack/react-router";
import { AlertTriangle, Plug, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { BackendStatus } from "@/lib/api/hooks";
import { cn } from "@/lib/utils";

/** Card shown when the surveillance backend is not configured or unreachable. */
export function BackendStateCard({
  status,
  baseUrl,
  onRetry,
  className,
}: {
  status: BackendStatus | "checking";
  baseUrl: string;
  onRetry?: () => void;
  className?: string;
}) {
  // Nothing to show while ready or still checking.
  if (status === "ready" || status === "checking") return null;

  return (
    <Alert
      className={cn(
        "border-dashed bg-card",
        status === "unreachable" ? "border-warning/50" : "border-border",
        className,
      )}
    >
      {status === "unreachable" ? (
        <AlertTriangle className="size-4 text-warning-foreground" />
      ) : (
        <Plug className="size-4 text-muted-foreground" />
      )}
      <AlertTitle>
        {status === "unconfigured"
          ? "Surveillance backend not connected"
          : "Surveillance backend unreachable"}
      </AlertTitle>
      <AlertDescription className="max-w-prose space-y-3 text-sm">
        <p>
          {status === "unconfigured"
            ? "VirusWatch displays only data served by the surveillance backend — nothing is simulated. Connect the FastAPI backend endpoint to fill this page."
            : `No response from ${baseUrl || "the configured endpoint"}. The service may be starting, offline, or blocking this origin.`}
        </p>
        <div className="flex flex-wrap gap-2">
          <Button asChild size="sm" variant="outline">
            <Link to="/settings">
              <Plug />
              Configure API endpoint
            </Link>
          </Button>
          {onRetry ? (
            <Button size="sm" variant="ghost" onClick={onRetry}>
              <RefreshCw />
              Retry
            </Button>
          ) : null}
        </div>
      </AlertDescription>
    </Alert>
  );
}

/** Section-level placeholder when one endpoint is not (yet) provided. */
export function UnavailableSection({
  title,
  endpoint,
  reason,
}: {
  title: string;
  endpoint?: string;
  reason?: string;
}) {
  return (
    <div className="flex flex-col items-start gap-1.5 rounded-lg border border-dashed border-border bg-muted/40 p-5">
      <p className="text-sm font-medium text-foreground">{title}</p>
      <p className="text-sm text-muted-foreground">
        {reason ??
          "This section activates once the backend provides its endpoint."}
        {endpoint ? (
          <span className="ml-1 font-mono text-xs text-muted-foreground/80">{endpoint}</span>
        ) : null}
      </p>
    </div>
  );
}

export function EmptyState({
  title,
  description,
}: {
  title: string;
  description?: string | undefined;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-border bg-card p-10 text-center">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description ? <p className="max-w-md text-sm text-muted-foreground">{description}</p> : null}
    </div>
  );
}

export function ErrorState({
  title = "Could not load data",
  description,
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-10 text-center">
      <div>
        <p className="text-sm font-medium text-foreground">{title}</p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">
          {description ?? "The backend returned an error. No substitute data is shown."}
        </p>
      </div>
      {onRetry ? (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw />
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export function DashboardSkeleton({ count = 5, className }: { count?: number; className?: string }) {
  return (
    <div className={cn("grid gap-4", className ?? "sm:grid-cols-2 xl:grid-cols-5")}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="space-y-3 rounded-xl border border-border bg-card p-5">
          <Skeleton className="h-3.5 w-28" />
          <Skeleton className="h-8 w-20" />
          <Skeleton className="h-3 w-24" />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2 rounded-xl border border-border bg-card p-4">
      <Skeleton className="h-9 w-full" />
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}
