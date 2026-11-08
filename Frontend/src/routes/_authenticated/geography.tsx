import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { MapPin } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { SurveillanceLineChart } from "@/components/charts";
import {
  BackendStateCard,
  EmptyState,
  ErrorState,
  TableSkeleton,
  UnavailableSection,
} from "@/components/data-states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useBackendQuery, useBackendStatus } from "@/lib/api/hooks";
import { api, type ObservationQuery } from "@/lib/api/endpoints";
import { formatValueWithUnit, geographicLevelLabel } from "@/lib/format";
import type { TimeSeriesPoint } from "@/lib/api/types";

export const Route = createFileRoute("/_authenticated/geography")({
  head: () => ({
    meta: [
      { title: "Geographic Explorer — VirusWatch" },
      { name: "description", content: "Compare surveillance indicators across regions with explicit geographic levels." },
      { property: "og:title", content: "Geographic Explorer — VirusWatch" },
      { property: "og:description", content: "Compare surveillance indicators across regions with explicit geographic levels." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: GeographyPage,
});

function GeographyPage() {
  const { status, baseUrl, recheck } = useBackendStatus();
  const [regionId, setRegionId] = useState("");
  const [indicator, setIndicator] = useState("");

  const geographies = useBackendQuery(["geographies"], (signal) => api.getGeographies(signal), status);
  const region = geographies.data?.find((g) => g.id === regionId) ?? geographies.data?.[0];

  const observationsQuery: ObservationQuery = region
    ? { geography: region.name, page: 1, page_size: 200 }
    : {};
  const observations = useBackendQuery(
    ["observations-geo", region?.name ?? ""],
    (signal) => api.getObservations(observationsQuery, signal),
    status && region ? status : "unconfigured",
  );

  const rows = observations.data?.items ?? [];
  const indicators = Array.from(new Set(rows.map((r) => r.indicator ?? "").filter(Boolean)));
  const activeIndicator = indicator || indicators[0] || "";
  const seriesForChart: TimeSeriesPoint[] = rows
    .filter((r) => r.indicator === activeIndicator && r.value !== null)
    .sort((a, b) => (a.period_start ?? "").localeCompare(b.period_start ?? ""))
    .map((r) => ({
      period: (r.period_start ?? "").slice(0, 10),
      period_start: r.period_start,
      period_end: r.period_end,
      value: r.value,
      unit: r.unit,
    }));

  const ready = status === "ready";

  return (
    <AppLayout>
      <div className="space-y-5">
        <BackendStateCard status={status} baseUrl={baseUrl} onRetry={recheck} />

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {!ready ? (
            <>
              <UnavailableSection title="Region selector" endpoint="/api/geographies" />
              <UnavailableSection title="Comparison charts" endpoint="/api/observations" />
            </>
          ) : geographies.isError ? (
            <div className="md:col-span-2 xl:col-span-3">
              <ErrorState description={geographies.error.message} onRetry={() => geographies.refetch()} />
            </div>
          ) : !geographies.data?.length ? (
            <div className="md:col-span-2 xl:col-span-3">
              <EmptyState
                title="No regions reported yet"
                description="The backend has not returned any geographic coverage."
              />
            </div>
          ) : (
            geographies.data.slice(0, 6).map((geo) => (
              <button
                key={geo.id}
                type="button"
                onClick={() => setRegionId(geo.id)}
                className={`rounded-xl border bg-card p-4 text-left transition-colors ${
                  (region?.id ?? geographies.data?.[0]?.id) === geo.id
                    ? "border-primary/60 ring-1 ring-primary/30"
                    : "border-border hover:border-primary/40"
                }`}
              >
                <div className="flex items-center gap-2">
                  <MapPin className="size-4 text-primary" />
                  <p className="font-medium text-foreground">{geo.name}</p>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{geographicLevelLabel(geo.level)}</p>
                {geo.country ? (
                  <Badge variant="secondary" className="mt-2">
                    {geo.country}
                  </Badge>
                ) : null}
              </button>
            ))
          )}
        </div>

        <Card>
          <CardHeader className="flex-row items-start justify-between space-y-0">
            <div>
              <CardTitle className="text-base">
                {region ? `Observations — ${region.name}` : "Observations"}
              </CardTitle>
              <CardDescription>
                {region ? geographicLevelLabel(region.level) : "Select a region"} · values are shown
                only for the geographic unit they represent.
              </CardDescription>
            </div>
            {ready && indicators.length > 0 ? (
              <Select value={activeIndicator} onValueChange={setIndicator}>
                <SelectTrigger className="w-48" aria-label="Indicator">
                  <SelectValue placeholder="Indicator" />
                </SelectTrigger>
                <SelectContent>
                  {indicators.map((ind) => (
                    <SelectItem key={ind} value={ind}>
                      {ind}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
          </CardHeader>
          <CardContent className="space-y-5">
            {!ready || !region ? null : observations.isPending ? (
              <TableSkeleton rows={5} />
            ) : observations.isError ? (
              <ErrorState description={observations.error.message} onRetry={() => observations.refetch()} />
            ) : seriesForChart.length === 0 ? (
              <EmptyState
                title="No observations for this region"
                description="Nothing has been reported for this geographic unit yet."
              />
            ) : (
              <>
                <SurveillanceLineChart data={seriesForChart} seriesLabel={activeIndicator} />
                <div className="overflow-x-auto rounded-lg border border-border">
                  <Table>
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead>Indicator</TableHead>
                        <TableHead>Value</TableHead>
                        <TableHead>Period</TableHead>
                        <TableHead>Level</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {rows.slice(0, 25).map((row) => (
                        <TableRow key={row.id}>
                          <TableCell className="font-medium">{row.indicator ?? "—"}</TableCell>
                          <TableCell className="tnum">{formatValueWithUnit(row.value, row.unit)}</TableCell>
                          <TableCell className="tnum text-sm">{(row.period_start ?? "").slice(0, 10)}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {geographicLevelLabel(row.geographic_level)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <p className="text-xs text-muted-foreground">
                  National or provincial statistics are never presented as city-specific figures;
                  missing geographic data is not interpolated.
                </p>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
