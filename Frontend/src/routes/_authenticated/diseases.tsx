import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { ArrowRight, Search } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { BackendStateCard, EmptyState, ErrorState, TableSkeleton } from "@/components/data-states";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useBackendQuery, useBackendStatus } from "@/lib/api/hooks";
import { api } from "@/lib/api/endpoints";

export const Route = createFileRoute("/_authenticated/diseases")({
  head: () => ({
    meta: [
      { title: "Disease Explorer — VirusWatch" },
      { name: "description", content: "Explore diseases with surveillance history, anomalies and guidance." },
      { property: "og:title", content: "Disease Explorer — VirusWatch" },
      { property: "og:description", content: "Explore diseases with surveillance history, anomalies and guidance." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: DiseasesPage,
});

function DiseasesPage() {
  const { status, baseUrl, recheck } = useBackendStatus();
  const [search, setSearch] = useState("");
  const diseases = useBackendQuery(["diseases"], (signal) => api.getDiseases(signal), status);

  const filtered = useMemo(() => {
    const list = diseases.data ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (d) => d.name.toLowerCase().includes(q) || (d.category ?? "").toLowerCase().includes(q),
    );
  }, [diseases.data, search]);

  return (
    <AppLayout>
      <div className="space-y-5">
        <BackendStateCard status={status} baseUrl={baseUrl} onRetry={recheck} />
        <div className="relative w-full max-w-sm">
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
          <Input
            className="pl-8"
            placeholder="Search diseases…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {!status || status === "unconfigured" || status === "unreachable" || status === "checking" ? (
          status === "checking" ? (
            <TableSkeleton rows={4} />
          ) : null
        ) : diseases.isPending ? (
          <TableSkeleton rows={4} />
        ) : diseases.isError ? (
          <ErrorState description={diseases.error.message} onRetry={() => diseases.refetch()} />
        ) : filtered.length === 0 ? (
          <EmptyState
            title={search ? "No matching diseases" : "No diseases reported yet"}
            description={
              search
                ? undefined
                : "The surveillance backend has not returned any monitored diseases."
            }
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((disease) => (
              <Link
                key={disease.id}
                to="/diseases/$slug"
                params={{ slug: disease.slug }}
                className="group rounded-xl border border-border bg-card p-5 transition-colors hover:border-primary/50"
              >
                <div className="flex items-start justify-between gap-2">
                  <h2 className="font-display text-base font-semibold text-foreground">
                    {disease.name}
                  </h2>
                  <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:text-primary" />
                </div>
                {disease.category ? (
                  <Badge variant="secondary" className="mt-2">
                    {disease.category}
                  </Badge>
                ) : null}
                {disease.description ? (
                  <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">
                    {disease.description}
                  </p>
                ) : null}
              </Link>
            ))}
          </div>
        )}
      </div>
    </AppLayout>
  );
}
