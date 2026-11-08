import { createFileRoute, Link } from "@tanstack/react-router";
import { useState, type MouseEvent } from "react";
import { toast } from "sonner";
import { ArrowUpDown, Download, ExternalLink, Search } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import {
  BackendStateCard,
  EmptyState,
  ErrorState,
  TableSkeleton,
} from "@/components/data-states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { api, endpoints, type ObservationQuery } from "@/lib/api/endpoints";
import { getConfiguredBaseUrl } from "@/lib/api/client";
import { useGlobalFilters } from "@/lib/filters";
import {
  formatDate,
  formatDateTime,
  formatValueWithUnit,
  geographicLevelLabel,
} from "@/lib/format";
import type { Observation } from "@/lib/api/types";

export const Route = createFileRoute("/_authenticated/surveillance")({
  head: () => ({
    meta: [
      { title: "Surveillance Explorer — VirusWatch" },
      { name: "description", content: "Explore structured surveillance observations with full provenance." },
      { property: "og:title", content: "Surveillance Explorer — VirusWatch" },
      { property: "og:description", content: "Explore structured surveillance observations with full provenance." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: SurveillancePage,
});

const PAGE_SIZE = 20;

function SurveillancePage() {
  const { status, baseUrl, recheck } = useBackendStatus();
  const { filters, setFilter } = useGlobalFilters();

  const [indicator, setIndicator] = useState("");
  const [source, setSource] = useState("");
  const [quality, setQuality] = useState("");
  const [periodFrom, setPeriodFrom] = useState("");
  const [periodTo, setPeriodTo] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("period_start");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [detail, setDetail] = useState<Observation | null>(null);

  const diseases = useBackendQuery(["diseases"], (signal) => api.getDiseases(signal), status);

  const query: ObservationQuery = {
    disease: filters.disease || undefined,
    geography: filters.geography || undefined,
    indicator: indicator || undefined,
    source: source || undefined,
    quality: quality || undefined,
    period_from: periodFrom || undefined,
    period_to: periodTo || undefined,
    q: search || undefined,
    page,
    page_size: PAGE_SIZE,
    sort,
    order,
  };

  const observations = useBackendQuery(
    ["observations", JSON.stringify(query)],
    (signal) => api.getObservations(query, signal),
    status,
  );

  const rows = observations.data?.items ?? [];
  const total = observations.data?.total ?? null;
  const pageCount = total !== null ? Math.max(1, Math.ceil(total / PAGE_SIZE)) : null;

  function toggleSort(column: string) {
    if (sort === column) {
      setOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSort(column);
      setOrder("asc");
    }
    setPage(1);
  }

  async function exportCsv() {
    const base = getConfiguredBaseUrl();
    if (!base) return;
    try {
      const params = new URLSearchParams();
      Object.entries(query).forEach(([k, v]) => {
        if (v !== undefined && v !== null) params.set(k, String(v));
      });
      const response = await fetch(`${base}${endpoints.observationExport}?${params}`, {
        headers: { Accept: "text/csv" },
      });
      if (!response.ok) throw new Error(`Export failed (${response.status})`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "viruswatch-observations.csv";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Export failed");
    }
  }

  function openDetail(row: Observation, event: MouseEvent) {
    event.stopPropagation();
    setDetail(row);
  }

  const ready = status === "ready";

  return (
    <AppLayout>
      <div className="space-y-5">
        <BackendStateCard status={status} baseUrl={baseUrl} onRetry={recheck} />

        {/* Filters */}
        <div className="grid gap-3 rounded-xl border border-border bg-card p-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <FilterSelect
            label="Disease"
            value={filters.disease}
            onChange={(v) => {
              setFilter("disease", v);
              setPage(1);
            }}
            placeholder="All diseases"
            options={(diseases.data ?? []).map((d) => ({ value: d.slug, label: d.name }))}
            disabled={!ready}
          />
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Geography</Label>
            <Input
              placeholder="e.g. Karachi"
              value={filters.geography}
              onChange={(e) => {
                setFilter("geography", e.target.value);
                setPage(1);
              }}
              disabled={!ready}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Indicator</Label>
            <Input
              placeholder="e.g. Influenza A"
              value={indicator}
              onChange={(e) => {
                setIndicator(e.target.value);
                setPage(1);
              }}
              disabled={!ready}
            />
          </div>
          <FilterSelect
            label="Quality"
            value={quality}
            onChange={(v) => {
              setQuality(v);
              setPage(1);
            }}
            placeholder="Any quality"
            options={[
              { value: "ok", label: "OK" },
              { value: "stale", label: "Stale" },
              { value: "incomplete", label: "Incomplete" },
              { value: "unverified", label: "Unverified" },
            ]}
            disabled={!ready}
          />
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Period from</Label>
            <Input
              type="date"
              value={periodFrom}
              onChange={(e) => {
                setPeriodFrom(e.target.value);
                setPage(1);
              }}
              disabled={!ready}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Period to</Label>
            <Input
              type="date"
              value={periodTo}
              onChange={(e) => {
                setPeriodTo(e.target.value);
                setPage(1);
              }}
              disabled={!ready}
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full max-w-xs">
            <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
            <Input
              className="pl-8"
              placeholder="Search observations…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              disabled={!ready}
            />
          </div>
          <Button variant="outline" size="sm" onClick={exportCsv} disabled={!ready}>
            <Download />
            Export CSV
          </Button>
          <p className="ml-auto text-xs text-muted-foreground">
            Indicators differ in definition and denominator — compare with care.
          </p>
        </div>

        {/* Table */}
        {!ready ? (
          <TableSkeleton rows={8} />
        ) : observations.isPending ? (
          <TableSkeleton rows={8} />
        ) : observations.isError ? (
          <ErrorState description={observations.error.message} onRetry={() => observations.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No observations match"
            description="No surveillance observations were returned for these filters."
          />
        ) : (
          <div className="overflow-x-auto rounded-xl border border-border bg-card">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <SortableHead label="Disease / indicator" column="disease_name" sort={sort} order={order} onSort={toggleSort} />
                  <TableHead>Geography</TableHead>
                  <SortableHead label="Observed value" column="value" sort={sort} order={order} onSort={toggleSort} />
                  <TableHead>Reporting period</TableHead>
                  <SortableHead label="Published" column="source_publication_date" sort={sort} order={order} onSort={toggleSort} />
                  <TableHead>Retrieved</TableHead>
                  <TableHead>Quality</TableHead>
                  <TableHead>Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow
                    key={row.id}
                    className="cursor-pointer"
                    onClick={() => setDetail(row)}
                  >
                    <TableCell>
                      <p className="font-medium text-foreground">{row.disease_name ?? "—"}</p>
                      <p className="text-xs text-muted-foreground">{row.indicator ?? "—"}</p>
                    </TableCell>
                    <TableCell>
                      <p>{row.geography_name ?? "—"}</p>
                      <p className="text-xs text-muted-foreground">
                        {geographicLevelLabel(row.geographic_level)}
                      </p>
                    </TableCell>
                    <TableCell className="tnum">
                      {formatValueWithUnit(row.value, row.unit)}
                      {row.denominator ? (
                        <p className="text-xs text-muted-foreground">per {row.denominator}</p>
                      ) : null}
                    </TableCell>
                    <TableCell className="tnum text-sm">
                      {formatDate(row.period_start)}
                      {row.period_end && row.period_end !== row.period_start
                        ? ` – ${formatDate(row.period_end)}`
                        : ""}
                    </TableCell>
                    <TableCell className="tnum text-sm">{formatDate(row.source_publication_date)}</TableCell>
                    <TableCell className="tnum text-sm">{formatDateTime(row.retrieved_at)}</TableCell>
                    <TableCell>
                      <QualityBadge status={row.quality_status} />
                    </TableCell>
                    <TableCell>
                      {row.source_url ? (
                        <Button asChild variant="ghost" size="sm" onClick={openDetail.bind(null, row)}>
                          <a href={row.source_url} target="_blank" rel="noreferrer noopener">
                            <ExternalLink />
                            Source
                          </a>
                        </Button>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {/* Pagination */}
        {ready && total !== null && pageCount !== null ? (
          <div className="flex items-center justify-between">
            <p className="tnum text-sm text-muted-foreground">
              {total.toLocaleString()} observations
            </p>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <span className="tnum text-sm">
                Page {page} of {pageCount}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= pageCount}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        ) : null}
      </div>

      {/* Provenance dialog */}
      <Dialog open={!!detail} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Observation provenance</DialogTitle>
            <DialogDescription>
              Where this value came from and how it was obtained.
            </DialogDescription>
          </DialogHeader>
          {detail ? (
            <dl className="space-y-3 text-sm">
              <ProvenanceRow label="Disease / indicator" value={`${detail.disease_name ?? "—"} · ${detail.indicator ?? "—"}`} />
              <ProvenanceRow label="Geography" value={`${detail.geography_name ?? "—"} — ${geographicLevelLabel(detail.geographic_level)}`} />
              <ProvenanceRow
                label="Observed value"
                value={formatValueWithUnit(detail.value, detail.unit)}
                extra={detail.denominator ? `per ${detail.denominator}` : undefined}
              />
              <ProvenanceRow label="Reporting period" value={`${formatDate(detail.period_start)} – ${formatDate(detail.period_end)}`} />
              <ProvenanceRow label="Source" value={detail.source_name ?? "—"} />
              <ProvenanceRow label="Publication date" value={formatDate(detail.source_publication_date)} />
              <ProvenanceRow label="Retrieved" value={formatDateTime(detail.retrieved_at)} />
              <ProvenanceRow label="Quality status" value={detail.quality_status ?? "—"} />
              {detail.source_url ? (
                <div>
                  <dt className="text-xs text-muted-foreground">Source link</dt>
                  <dd>
                    <a
                      href={detail.source_url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-center gap-1 font-medium text-primary underline-offset-4 hover:underline"
                    >
                      Open original source
                      <ExternalLink className="size-3.5" />
                    </a>
                  </dd>
                </div>
              ) : null}
              <p className="text-xs text-muted-foreground">
                Values come from official surveillance sources with different definitions and
                reporting cadences. Do not compare incompatible indicators across regions.
              </p>
            </dl>
          ) : null}
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
  placeholder,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  placeholder: string;
  disabled?: boolean | undefined;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <Select value={value || "__all"} onValueChange={(v) => onChange(v === "__all" ? "" : v)} disabled={disabled ?? false}>
        <SelectTrigger aria-label={label}>
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all">{placeholder}</SelectItem>
          {options.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function SortableHead({
  label,
  column,
  sort,
  order,
  onSort,
}: {
  label: string;
  column: string;
  sort: string;
  order: "asc" | "desc";
  onSort: (column: string) => void;
}) {
  const active = sort === column;
  return (
    <TableHead>
      <button
        type="button"
        onClick={() => onSort(column)}
        className={`inline-flex items-center gap-1 ${active ? "font-semibold text-foreground" : "text-muted-foreground hover:text-foreground"}`}
      >
        {label}
        <ArrowUpDown className="size-3" />
        {active ? <span className="sr-only">({order === "asc" ? "ascending" : "descending"})</span> : null}
      </button>
    </TableHead>
  );
}

function QualityBadge({ status }: { status?: string | null | undefined }) {
  if (!status) return <span className="text-muted-foreground">—</span>;
  const tone =
    status === "ok"
      ? "border-positive/30 bg-positive/10 text-positive-foreground"
      : status === "stale" || status === "incomplete"
        ? "border-warning/40 bg-warning/10 text-warning-foreground"
        : "border-border bg-muted text-muted-foreground";
  return (
    <Badge variant="outline" className={tone}>
      {status}
    </Badge>
  );
}

function ProvenanceRow({
  label,
  value,
  extra,
}: {
  label: string;
  value: string;
  extra?: string | undefined;
}) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium text-foreground">
        {value}
        {extra ? <span className="ml-1 font-normal text-muted-foreground">({extra})</span> : null}
      </dd>
    </div>
  );
}
