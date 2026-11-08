import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Activity, Database, MapPin, ShieldAlert, Timer } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { SurveillanceLineChart } from "@/components/charts";
import {
  BackendStateCard,
  DashboardSkeleton,
  EmptyState,
  ErrorState,
  UnavailableSection,
} from "@/components/data-states";
import { FreshnessBadge } from "@/components/freshness-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useBackendQuery, useBackendStatus } from "@/lib/api/hooks";
import { api } from "@/lib/api/endpoints";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import type { TimeSeriesPoint } from "@/lib/api/types";

export const Route = createFileRoute("/_authenticated/dashboard")({
  head: () => ({
    meta: [
      { title: "Overview — VirusWatch" },
      { name: "description", content: "Surveillance overview: monitored diseases, regions, anomalies and data freshness." },
      { property: "og:title", content: "Overview — VirusWatch" },
      { property: "og:description", content: "Surveillance overview: monitored diseases, regions, anomalies and data freshness." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: DashboardPage,
});

function DashboardPage() {
  const { status, baseUrl, recheck } = useBackendStatus();

  const summary = useBackendQuery(["summary"], (signal) => api.getSummary(signal), status);
  const freshness = useBackendQuery(["freshness"], (signal) => api.getFreshness(signal), status);
  const activity = useBackendQuery(["activity"], (signal) => api.getActivity(signal), status);
  const diseases = useBackendQuery(["diseases"], (signal) => api.getDiseases(signal), status);

  const [selectedDisease, setSelectedDisease] = useState("");
  const diseaseSlug = selectedDisease || diseases.data?.[0]?.slug || "";
  const series = useBackendQuery(
    ["disease-series", diseaseSlug],
    (signal) => api.getDiseaseSeries(diseaseSlug, signal) as Promise<TimeSeriesPoint[]>,
    status && diseaseSlug ? status : "unconfigured",
  );

  const ready = status === "ready";

  return (
    <AppLayout>
      <div className="space-y-6">
        <BackendStateCard status={status} baseUrl={baseUrl} onRetry={recheck} />

        {/* Summary cards */}
        {!ready ? (
          <DashboardSkeleton />
        ) : summary.isError ? (
          <ErrorState description={summary.error.message} onRetry={() => summary.refetch()} />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <SummaryCard
              icon={Activity}
              label="Diseases monitored"
              value={summary.data?.diseases_monitored}
              note="Reported by backend"
            />
            <SummaryCard
              icon={MapPin}
              label="Reporting regions"
              value={summary.data?.regions_represented}
              note="Reported by backend"
            />
            <SummaryCard
              icon={Timer}
              label="Latest observation period"
              value={summary.data?.latest_observation_period}
              note={undefined}
              raw
            />
            <SummaryCard
              icon={ShieldAlert}
              label="Validated anomalies"
              value={summary.data?.validated_anomalies}
              note="Statistical, not confirmed outbreaks"
            />
            <SummaryCard
              icon={Database}
              label="Stale or incomplete sources"
              value={summary.data?.stale_or_incomplete_sources}
              note="Reported by backend"
            />
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-3">
          {/* Trend chart */}
          <Card className="lg:col-span-2">
            <CardHeader className="flex-row items-start justify-between space-y-0">
              <div>
                <CardTitle className="text-base">Historical surveillance trends</CardTitle>
                <CardDescription>
                  Observations over available reporting periods. Gaps are shown as missing, not zero.
                </CardDescription>
              </div>
              {ready && diseases.data && diseases.data.length > 0 ? (
                <Select value={selectedDisease || diseases.data?.[0]?.slug || ""} onValueChange={setSelectedDisease}>
                  <SelectTrigger className="w-44" aria-label="Select disease">
                    <SelectValue placeholder="Disease" />
                  </SelectTrigger>
                  <SelectContent>
                    {diseases.data.map((d) => (
                      <SelectItem key={d.id} value={d.slug}>
                        {d.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : null}
            </CardHeader>
            <CardContent>
              {!ready ? (
                <UnavailableSection title="Trend chart" endpoint={"/api/diseases/{slug}/series"} />
              ) : !diseaseSlug ? (
                <EmptyState title="No diseases reported yet" description="The backend has not returned any monitored diseases." />
              ) : series.isPending ? (
                <div className="h-[260px] animate-pulse rounded-lg bg-muted" />
              ) : series.isError ? (
                <ErrorState description={series.error.message} onRetry={() => series.refetch()} />
              ) : !series.data || series.data.length === 0 ? (
                <EmptyState title="No observations available" description="This disease has no recorded observations for the available periods." />
              ) : (
                <SurveillanceLineChart data={series.data} seriesLabel={diseases.data?.find((d) => d.slug === diseaseSlug)?.name ?? "Observations"} />
              )}
            </CardContent>
          </Card>

          {/* Data status */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Data status</CardTitle>
              <CardDescription>Cadence and freshness of the underlying sources.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {!ready ? (
                <UnavailableSection title="Data status" endpoint="/api/freshness" />
              ) : freshness.isError ? (
                <ErrorState description={freshness.error.message} onRetry={() => freshness.refetch()} />
              ) : (
                <>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm text-muted-foreground">Cadence</span>
                    <FreshnessBadge
                      cadence={freshness.data?.reporting_cadence}
                      lastRetrieval={freshness.data?.last_successful_retrieval}
                      freshness={freshness.data?.freshness_status}
                    />
                  </div>
                  <DataStatusRow
                    label="Last successful retrieval"
                    value={formatDateTime(freshness.data?.last_successful_retrieval)}
                  />
                  <DataStatusRow
                    label="Source publication date"
                    value={formatDateTime(freshness.data?.last_source_publication_date)}
                  />
                  <DataStatusRow
                    label="Reporting period"
                    value={freshness.data?.current_reporting_period ?? "—"}
                  />
                  <div className="space-y-1.5">
                    <p className="text-sm text-muted-foreground">Missing or delayed sources</p>
                    {freshness.data?.missing_or_delayed_sources?.length ? (
                      <div className="flex flex-wrap gap-1.5">
                        {freshness.data.missing_or_delayed_sources.map((s) => (
                          <Badge key={s} variant="outline" className="border-warning/40 bg-warning/10 text-warning-foreground">
                            {s}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-foreground/80">None reported</p>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Surveillance data follows official reporting cadences and is never presented
                    as real-time.
                  </p>
                </>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Recent activity */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent activity</CardTitle>
            <CardDescription>Source updates, ingestion runs, published reports and validated anomaly events.</CardDescription>
          </CardHeader>
          <CardContent>
            {!ready ? (
              <UnavailableSection title="Recent activity" endpoint="/api/activity" />
            ) : activity.isError ? (
              <ErrorState description={activity.error.message} onRetry={() => activity.refetch()} />
            ) : !activity.data?.length ? (
              <EmptyState title="No recent activity" description="The backend has not reported any recent events." />
            ) : (
              <ol className="relative space-y-4 border-l border-border pl-4">
                {activity.data.map((event) => (
                  <li key={event.id} className="relative">
                    <span className="absolute -left-[21px] top-1 size-2.5 rounded-full border-2 border-background bg-primary" />
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="text-sm font-medium text-foreground">{event.title}</p>
                      <span className="text-xs text-muted-foreground">{relativeTime(event.occurred_at)}</span>
                    </div>
                    {event.detail ? (
                      <p className="mt-0.5 text-sm text-muted-foreground">{event.detail}</p>
                    ) : null}
                    {event.kind ? (
                      <Badge variant="secondary" className="mt-1.5">
                        {event.kind}
                      </Badge>
                    ) : null}
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}

function SummaryCard({
  icon: Icon,
  label,
  value,
  note,
  raw,
}: {
  icon: typeof Activity;
  label: string;
  value?: string | number | null | undefined;
  note?: string | undefined;
  raw?: boolean | undefined;
}) {
  const display = raw ? (value ?? "—") : formatNumber(value as number | null);
  return (
    <Card>
      <CardContent className="space-y-1.5 p-5">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Icon className="size-4" />
          <span className="text-xs font-medium uppercase tracking-wide">{label}</span>
        </div>
        <p className="tnum text-2xl font-semibold text-foreground">{display}</p>
        {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
      </CardContent>
    </Card>
  );
}

function DataStatusRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-border pb-2 last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="tnum text-sm font-medium text-foreground">{value}</span>
    </div>
  );
}
