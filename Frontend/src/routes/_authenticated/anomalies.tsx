import { createFileRoute } from "@tanstack/react-router";
import { AlertTriangle, Info } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { SurveillanceLineChart } from "@/components/charts";
import {
  BackendStateCard,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from "@/components/data-states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useBackendQuery, useBackendStatus } from "@/lib/api/hooks";
import { api } from "@/lib/api/endpoints";
import { formatPeriod, formatValueWithUnit, relativeTime } from "@/lib/format";
import type { Anomaly } from "@/lib/api/types";

export const Route = createFileRoute("/_authenticated/anomalies")({
  head: () => ({
    meta: [
      { title: "Trends & Anomalies — VirusWatch" },
      { name: "description", content: "Backend-generated statistical anomalies with methodology, baselines and limitations." },
      { property: "og:title", content: "Trends & Anomalies — VirusWatch" },
      { property: "og:description", content: "Backend-generated statistical anomalies with methodology, baselines and limitations." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: AnomaliesPage,
});

function AnomaliesPage() {
  const { status, baseUrl, recheck } = useBackendStatus();
  const ready = status === "ready";
  const anomalies = useBackendQuery(["anomalies"], (signal) => api.getAnomalies(signal), status);

  return (
    <AppLayout>
      <div className="space-y-5">
        <BackendStateCard status={status} baseUrl={baseUrl} onRetry={recheck} />

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Statistical anomalies</CardTitle>
            <CardDescription>
              Unusual changes detected by the backend's documented methods. A validated anomaly
              is not an officially confirmed outbreak.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!ready ? (
              <TableSkeleton rows={3} />
            ) : anomalies.isPending ? (
              <TableSkeleton rows={3} />
            ) : anomalies.isError ? (
              <ErrorState description={anomalies.error.message} onRetry={() => anomalies.refetch()} />
            ) : anomalies.data.length === 0 ? (
              <EmptyState
                title="No anomalies detected"
                description="The backend has not reported any validated statistical anomalies."
              />
            ) : (
              anomalies.data.map((anomaly) => <AnomalyCard key={anomaly.id} anomaly={anomaly} />)
            )}
          </CardContent>
        </Card>

        <p className="flex items-start gap-2 rounded-lg border border-border bg-muted/40 p-4 text-xs leading-relaxed text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" />
          Labels such as "Unusual increase detected" describe a statistical finding. "Outbreak
          confirmed" is used only when an authoritative source explicitly confirms an outbreak.
          No risk scores or predictions are generated in the interface — only results the backend
          documents and returns.
        </p>
      </div>
    </AppLayout>
  );
}

function AnomalyCard({ anomaly }: { anomaly: Anomaly }) {
  const isIncrease = anomaly.direction === "increase";
  return (
    <div className="rounded-xl border border-border bg-background p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant="outline"
          className={
            isIncrease
              ? "border-destructive/40 bg-destructive/10 text-destructive"
              : "border-border bg-muted text-muted-foreground"
          }
        >
          <AlertTriangle className="size-3" />
          {isIncrease
            ? "Unusual increase detected"
            : anomaly.direction === "decrease"
              ? "Unusual decrease detected"
              : "Unusual change detected"}
        </Badge>
        {anomaly.status ? <Badge variant="secondary">{anomaly.status}</Badge> : null}
        <span className="text-xs text-muted-foreground">{relativeTime(anomaly.created_at)}</span>
      </div>

      <p className="mt-2.5 font-display text-base font-semibold text-foreground">
        {anomaly.disease_name ?? "Unknown disease"} — {anomaly.geography_name ?? "Unknown region"}
      </p>
      <p className="text-sm text-muted-foreground">
        {formatPeriod(anomaly.period_start, anomaly.period_end)}
        {anomaly.indicator ? ` · ${anomaly.indicator}` : ""}
      </p>

      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">Observed value</dt>
          <dd className="tnum font-medium">{formatValueWithUnit(anomaly.observed_value, anomaly.unit)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Baseline</dt>
          <dd className="tnum font-medium">{formatValueWithUnit(anomaly.baseline_value, anomaly.unit)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Magnitude / direction</dt>
          <dd className="tnum font-medium">
            {anomaly.magnitude ?? "—"} {anomaly.direction ?? ""}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Detection method</dt>
          <dd className="font-medium">
            {anomaly.detection_method ?? "—"}
            {anomaly.threshold ? ` · threshold ${anomaly.threshold}` : ""}
          </dd>
        </div>
      </dl>

      {anomaly.history && anomaly.history.length > 0 ? (
        <div className="mt-4 rounded-lg border border-border bg-card p-3">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Supporting history
          </p>
          <SurveillanceLineChart
            data={anomaly.history}
            seriesLabel={anomaly.indicator ?? "Observed value"}
            height={180}
          />
        </div>
      ) : null}

      {anomaly.interpretation ? (
        <p className="mt-3 text-sm text-muted-foreground">{anomaly.interpretation}</p>
      ) : null}
      {anomaly.limitations ? (
        <p className="mt-1.5 text-xs text-muted-foreground">Limitations: {anomaly.limitations}</p>
      ) : null}
    </div>
  );
}
