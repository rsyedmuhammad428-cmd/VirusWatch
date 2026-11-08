import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Download, ExternalLink, FileText } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { BackendStateCard, EmptyState, ErrorState, TableSkeleton, UnavailableSection } from "@/components/data-states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useBackendQuery, useBackendStatus } from "@/lib/api/hooks";
import { api } from "@/lib/api/endpoints";
import { formatDate } from "@/lib/format";

export const Route = createFileRoute("/_authenticated/reports")({
  head: () => ({
    meta: [
      { title: "Reports and Research — VirusWatch" },
      { name: "description", content: "Browse official reports and research documents with scope, period and source links." },
      { property: "og:title", content: "Reports and Research — VirusWatch" },
      { property: "og:description", content: "Browse official reports and research documents with scope, period and source links." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ReportsPage,
});

function ReportsPage() {
  const { status, baseUrl, recheck } = useBackendStatus();
  const ready = status === "ready";
  const reports = useBackendQuery(["reports"], (signal) => api.getReports(signal), status);

  async function download(url: string, filename: string) {
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(objectUrl);
    } catch {
      // Cross-origin downloads without CORS fall back to opening the file.
      window.open(url, "_blank", "noopener");
    }
  }

  return (
    <AppLayout>
      <div className="space-y-5">
        <BackendStateCard status={status} baseUrl={baseUrl} onRetry={recheck} />

        <Card>
          <CardHeader className="flex-row items-start justify-between space-y-0">
            <div>
              <CardTitle className="text-base">Reports and research documents</CardTitle>
              <CardDescription>
                Official bulletins, publications and research documents with their scope and reporting period.
              </CardDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={!ready}
              onClick={() => toast.info("Research export becomes available with the backend export service.")}
              title="Requires the backend's export endpoint"
            >
              <Download />
              Export research data
            </Button>
          </CardHeader>
          <CardContent>
            {!ready ? (
              <UnavailableSection title="Report library" endpoint="/api/reports" />
            ) : reports.isPending ? (
              <TableSkeleton rows={5} />
            ) : reports.isError ? (
              <ErrorState description={reports.error.message} onRetry={() => reports.refetch()} />
            ) : reports.data.length === 0 ? (
              <EmptyState
                title="No reports published yet"
                description="The backend has not returned any research documents."
              />
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>Report</TableHead>
                      <TableHead>Organization</TableHead>
                      <TableHead>Published</TableHead>
                      <TableHead>Geographic scope</TableHead>
                      <TableHead>Reporting period</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {reports.data.map((report) => (
                      <TableRow key={report.id}>
                        <TableCell className="max-w-[280px]">
                          <p className="truncate font-medium text-foreground" title={report.title}>
                            {report.title}
                          </p>
                          {report.summary ? (
                            <p className="truncate text-xs text-muted-foreground" title={report.summary}>
                              {report.summary}
                            </p>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-sm">{report.organization ?? "—"}</TableCell>
                        <TableCell className="tnum text-sm">{formatDate(report.publication_date)}</TableCell>
                        <TableCell className="text-sm">{report.geographic_scope ?? "—"}</TableCell>
                        <TableCell className="text-sm">{report.reporting_period ?? "—"}</TableCell>
                        <TableCell className="text-sm">{report.document_type ?? "—"}</TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-1">
                            {report.download_url ? (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => download(report.download_url!, report.title)}
                              >
                                <Download />
                                Download
                              </Button>
                            ) : null}
                            {report.source_url ? (
                              <Button asChild variant="ghost" size="sm">
                                <a href={report.source_url} target="_blank" rel="noreferrer noopener">
                                  <ExternalLink />
                                  Source
                                </a>
                              </Button>
                            ) : null}
                            {!report.download_url && !report.source_url ? (
                              <span className="text-muted-foreground">—</span>
                            ) : null}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>

        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <FileText className="size-3.5" />
          Methodology and limitations accompany exported analysis when the backend provides them;
          no research results or model metrics are generated in the interface.
        </p>
      </div>
    </AppLayout>
  );
}
