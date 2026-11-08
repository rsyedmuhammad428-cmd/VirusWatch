import { createFileRoute } from "@tanstack/react-router";
import { ExternalLink, FileUp, RefreshCw, Settings2 } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { BackendStateCard, EmptyState, ErrorState, TableSkeleton, UnavailableSection } from "@/components/data-states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useBackendQuery, useBackendStatus } from "@/lib/api/hooks";
import { api } from "@/lib/api/endpoints";
import { formatDateTime, formatNumber, relativeTime } from "@/lib/format";
import type { DataSource } from "@/lib/api/types";

export const Route = createFileRoute("/_authenticated/data-sources")({
  head: () => ({
    meta: [
      { title: "Data Sources — VirusWatch" },
      { name: "description", content: "Connected surveillance sources, their coverage and ingestion status." },
      { property: "og:title", content: "Data Sources — VirusWatch" },
      { property: "og:description", content: "Connected surveillance sources, their coverage and ingestion status." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: DataSourcesPage,
});

function DataSourcesPage() {
  const { status, baseUrl, recheck } = useBackendStatus();
  const ready = status === "ready";
  const sources = useBackendQuery(["sources"], (signal) => api.getSources(signal), status);

  return (
    <AppLayout>
      <div className="space-y-5">
        <BackendStateCard status={status} baseUrl={baseUrl} onRetry={recheck} />

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Surveillance sources</CardTitle>
            <CardDescription>
              Organizations whose data feeds VirusWatch, with freshness and ingestion status.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!ready ? (
              <UnavailableSection title="Source registry" endpoint="/api/sources" />
            ) : sources.isPending ? (
              <TableSkeleton rows={4} />
            ) : sources.isError ? (
              <ErrorState description={sources.error.message} onRetry={() => sources.refetch()} />
            ) : sources.data.length === 0 ? (
              <EmptyState
                title="No sources registered"
                description="The backend has not returned any surveillance sources."
              />
            ) : (
              sources.data.map((source) => <SourceCard key={source.id} source={source} />)
            )}
          </CardContent>
        </Card>

        <AdminControlsPanel ready={ready} />
      </div>
    </AppLayout>
  );
}

function SourceCard({ source }: { source: DataSource }) {
  const freshness = source.freshness_status;
  const tone =
    freshness === "fresh"
      ? "border-positive/30 bg-positive/10 text-positive-foreground"
      : freshness === "stale" || freshness === "delayed"
        ? "border-warning/40 bg-warning/10 text-warning-foreground"
        : "border-border bg-muted text-muted-foreground";

  return (
    <div className="rounded-xl border border-border bg-background p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium text-foreground">{source.name}</p>
        {source.organization ? <span className="text-sm text-muted-foreground">· {source.organization}</span> : null}
        <Badge variant="outline" className={`ml-auto ${tone}`}>
          {freshness ?? "status unknown"}
        </Badge>
      </div>
      {source.url ? (
        <a
          href={source.url}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-1 inline-flex items-center gap-1 text-xs text-primary underline-offset-4 hover:underline"
        >
          {source.url}
          <ExternalLink className="size-3" />
        </a>
      ) : null}
      <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <p className="text-xs text-muted-foreground">Coverage</p>
          <p>{[...(source.supported_geographies ?? []), ...(source.supported_indicators ?? [])].join(", ") || "—"}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Last successful ingestion</p>
          <p className="tnum">{relativeTime(source.last_successful_ingestion)} · {formatDateTime(source.last_successful_ingestion)}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Last attempt</p>
          <p className="tnum">{relativeTime(source.last_attempted_ingestion)}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Records</p>
          <p className="tnum">{formatNumber(source.record_count)}</p>
        </div>
      </div>
      {source.ingestion_error ? (
        <p className="mt-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          Ingestion error: {source.ingestion_error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Administrative controls. Only meaningful with an admin role AND backend
 * endpoints that support these operations; the interface never exposes
 * private storage or privileged actions to ordinary users.
 */
function AdminControlsPanel({ ready }: { ready: boolean }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Settings2 className="size-4 text-primary" />
          Administrative controls
        </CardTitle>
        <CardDescription>
          Visible with administrator authorization; actions additionally require backend support.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!ready ? (
          <UnavailableSection title="Administrative controls" endpoint="/api/sources (admin endpoints)" />
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled title="Requires an admin document-upload endpoint">
              <FileUp />
              Upload official PDF bulletin
            </Button>
            <Button variant="outline" size="sm" disabled title="Requires a backend ingestion endpoint">
              <RefreshCw />
              Trigger ingestion
            </Button>
            <Button variant="ghost" size="sm" disabled title="Requires backend job-status support">
              View processing queue
            </Button>
          </div>
        )}
        <p className="mt-3 text-xs text-muted-foreground">
          Document processing stages (queued, extracting, validating, indexed, completed, failed,
          requires review) render here once the backend exposes a job-status endpoint. Upload and
          ingestion actions stay disabled until those endpoints exist — no private storage or
          privileged function is reachable from this interface.
        </p>
      </CardContent>
    </Card>
  );
}
