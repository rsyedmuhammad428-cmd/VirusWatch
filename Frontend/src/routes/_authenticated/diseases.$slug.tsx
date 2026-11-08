import { createFileRoute, Link } from "@tanstack/react-router";
import { AlertTriangle, ArrowLeft, FileText, Info } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { SurveillanceLineChart } from "@/components/charts";
import {
  BackendStateCard,
  EmptyState,
  ErrorState,
  UnavailableSection,
} from "@/components/data-states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useBackendQuery, useBackendStatus } from "@/lib/api/hooks";
import { api } from "@/lib/api/endpoints";
import { formatPeriod, formatValueWithUnit, relativeTime } from "@/lib/format";
import type { TimeSeriesPoint } from "@/lib/api/types";

export const Route = createFileRoute("/_authenticated/diseases/$slug")({
  head: ({ params }) => ({
    meta: [
      { title: `Disease — VirusWatch` },
      { name: "description", content: "Disease surveillance detail: history, anomalies, guidance and sources." },
      { property: "og:title", content: `Disease — VirusWatch` },
      { property: "og:description", content: "Disease surveillance detail: history, anomalies, guidance and sources." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: DiseaseDetailPage,
});

function DiseaseDetailPage() {
  const { slug } = Route.useParams();
  const { status, baseUrl, recheck } = useBackendStatus();

  const disease = useBackendQuery(["disease", slug], (signal) => api.getDisease(slug, signal), status);
  const series = useBackendQuery(
    ["disease-series", slug],
    (signal) => api.getDiseaseSeries(slug, signal) as Promise<TimeSeriesPoint[]>,
    status,
  );
  const anomalies = useBackendQuery(["anomalies"], (signal) => api.getAnomalies(signal), status);
  const reports = useBackendQuery(["reports"], (signal) => api.getReports(signal), status);

  const name = disease.data?.name ?? slug;
  const relatedAnomalies = (anomalies.data ?? []).filter(
    (a) => (a.disease_name ?? "").toLowerCase() === name.toLowerCase(),
  );
  const relatedReports = (reports.data ?? []).filter(
    (r) => (r.title ?? "").toLowerCase().includes(name.toLowerCase()),
  );

  return (
    <AppLayout>
      <div className="space-y-5">
        <BackendStateCard status={status} baseUrl={baseUrl} onRetry={recheck} />

        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to="/diseases">
            <ArrowLeft />
            All diseases
          </Link>
        </Button>

        {disease.isPending ? (
          <div className="h-24 animate-pulse rounded-xl bg-muted" />
        ) : disease.isError ? (
          <ErrorState description={disease.error.message} onRetry={() => disease.refetch()} />
        ) : (
          <div className="space-y-2">
            <h1 className="font-display text-2xl font-bold tracking-tight text-foreground">
              {disease.data?.name ?? slug}
            </h1>
            {disease.data?.description ? (
              <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
                {disease.data.description}
              </p>
            ) : null}
            {disease.data?.description ? null : (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Info className="size-3.5" />
                Overview will appear when the backend provides verified source information.
              </p>
            )}
          </div>
        )}

        {/* Historical observations */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Historical observations</CardTitle>
            <CardDescription>
              Available reporting periods. Gaps are missing observations, not zeros.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {status !== "ready" ? (
              <UnavailableSection title="Trend chart" endpoint="/api/diseases/{slug}/series" />
            ) : series.isPending ? (
              <div className="h-[260px] animate-pulse rounded-lg bg-muted" />
            ) : series.isError ? (
              <ErrorState description={series.error.message} onRetry={() => series.refetch()} />
            ) : !series.data?.length ? (
              <EmptyState title="No observations available" />
            ) : (
              <>
                <SurveillanceLineChart data={series.data} seriesLabel={name} />
                <p className="tnum mt-3 text-xs text-muted-foreground">
                  {series.data.filter((p) => p.value !== null).length} of {series.data.length}{" "}
                  periods reported · unit: {series.data.find((p) => p.unit)?.unit ?? "—"}
                </p>
              </>
            )}
          </CardContent>
        </Card>

        {/* Regional comparison */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Regional comparisons</CardTitle>
            <CardDescription>
              Only shown where the backend confirms compatible, like-for-like indicators.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {status !== "ready" ? (
              <UnavailableSection title="Regional comparisons" reason="Requires region-resolved series from the backend." />
            ) : (
              <EmptyState
                title="No compatible regional series reported"
                description="The backend has not marked any regional comparison as compatible for this disease."
              />
            )}
          </CardContent>
        </Card>

        {/* Anomalies */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Validated anomalies</CardTitle>
            <CardDescription>
              Statistical detections with their methodology — distinct from confirmed outbreaks.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {status !== "ready" ? (
              <UnavailableSection title="Anomalies" endpoint="/api/anomalies" />
            ) : anomalies.isError ? (
              <ErrorState description={anomalies.error.message} onRetry={() => anomalies.refetch()} />
            ) : relatedAnomalies.length === 0 ? (
              <EmptyState title="No validated anomalies for this disease" />
            ) : (
              relatedAnomalies.map((a) => (
                <div key={a.id} className="rounded-lg border border-border bg-muted/40 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="border-warning/40 bg-warning/10 text-warning-foreground">
                      <AlertTriangle className="size-3" />
                      Unusual change detected
                    </Badge>
                    <span className="text-xs text-muted-foreground">{relativeTime(a.created_at)}</span>
                  </div>
                  <p className="mt-2 text-sm font-medium text-foreground">
                    {a.geography_name ?? "—"} · {formatPeriod(a.period_start, a.period_end)}
                  </p>
                  <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                    <div>
                      <dt className="text-xs text-muted-foreground">Observed</dt>
                      <dd className="tnum">{formatValueWithUnit(a.observed_value, a.unit)}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-muted-foreground">Baseline</dt>
                      <dd className="tnum">{formatValueWithUnit(a.baseline_value, a.unit)}</dd>
                    </div>
                    {a.detection_method ? (
                      <div>
                        <dt className="text-xs text-muted-foreground">Detection method</dt>
                        <dd>{a.detection_method}</dd>
                      </div>
                    ) : null}
                    {a.threshold ? (
                      <div>
                        <dt className="text-xs text-muted-foreground">Threshold</dt>
                        <dd className="tnum">{String(a.threshold)}</dd>
                      </div>
                    ) : null}
                  </dl>
                  {a.interpretation ? (
                    <p className="mt-2 text-sm text-muted-foreground">{a.interpretation}</p>
                  ) : null}
                  {a.limitations ? (
                    <p className="mt-1 text-xs text-muted-foreground">Limitations: {a.limitations}</p>
                  ) : null}
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {/* Related reports */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Related research documents</CardTitle>
          </CardHeader>
          <CardContent>
            {status !== "ready" ? (
              <UnavailableSection title="Related reports" endpoint="/api/reports" />
            ) : relatedReports.length === 0 ? (
              <EmptyState title="No related documents reported" />
            ) : (
              <ul className="divide-y divide-border">
                {relatedReports.map((r) => (
                  <li key={r.id} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-foreground">{r.title}</p>
                      <p className="text-xs text-muted-foreground">
                        {r.organization ?? "—"} · {r.publication_date ?? "—"}
                      </p>
                    </div>
                    {r.source_url ? (
                      <Button asChild variant="ghost" size="sm">
                        <a href={r.source_url} target="_blank" rel="noreferrer noopener">
                          <FileText />
                          Open
                        </a>
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
