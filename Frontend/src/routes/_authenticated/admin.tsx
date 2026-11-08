import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Lock, ShieldCheck } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { supabase } from "@/integrations/supabase/client";

export const Route = createFileRoute("/_authenticated/admin")({
  head: () => ({
    meta: [
      { title: "Administration — VirusWatch" },
      { name: "description", content: "Administrative console — restricted to authorized administrators." },
      { property: "og:title", content: "Administration — VirusWatch" },
      { property: "og:description", content: "Administrative console — restricted to authorized administrators." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: AdminPage,
});

function AdminPage() {
  // Authorization boundary: the role check re-runs here; hiding the sidebar
  // entry alone is never trusted.
  const { data: isAdmin, isPending } = useQuery({
    queryKey: ["my-roles"],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) return false;
      const { data: roles, error } = await supabase
        .from("user_roles")
        .select("role")
        .eq("user_id", user.id);
      if (error) return false;
      return roles.some((r) => r.role === "admin");
    },
  });

  if (isPending) {
    return (
      <AppLayout>
        <div className="h-40 animate-pulse rounded-xl bg-muted" />
      </AppLayout>
    );
  }

  if (!isAdmin) {
    return (
      <AppLayout>
        <Card className="mx-auto max-w-lg">
          <CardHeader className="items-center text-center">
            <span className="mx-auto flex size-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
              <Lock className="size-5" />
            </span>
            <CardTitle>Administrator authorization required</CardTitle>
            <CardDescription>
              This console is restricted to users holding the administrator role. The role is
              stored and enforced server-side; requesting access is done outside the app.
            </CardDescription>
          </CardHeader>
        </Card>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-5">
        <Card>
          <CardHeader className="flex-row items-center gap-3 space-y-0">
            <span className="flex size-10 items-center justify-center rounded-full bg-primary/10 text-primary">
              <ShieldCheck className="size-5" />
            </span>
            <div>
              <CardTitle className="text-base">Administrative console</CardTitle>
              <CardDescription>
                Signed in with the administrator role — server-side checks still apply to every
                privileged action.
              </CardDescription>
            </div>
            <Badge variant="outline" className="ml-auto border-positive/30 bg-positive/10 text-positive-foreground">
              admin
            </Badge>
          </CardHeader>
        </Card>

        <div className="grid gap-5 lg:grid-cols-2">
          <AdminConsoleSection
            title="Document processing"
            items={["Upload official PDF bulletins", "Review extraction jobs", "Failed or ambiguous extractions"]}
            note="Waiting for backend document-processing endpoints."
          />
          <AdminConsoleSection
            title="Ingestion and sources"
            items={["Trigger ingestion runs", "Inspect source errors", "Manage approved sources"]}
            note="Waiting for backend ingestion-management endpoints."
          />
          <AdminConsoleSection
            title="Processing stages"
            items={["Queued", "Extracting", "Validating", "Indexed", "Completed", "Failed", "Requires review"]}
            note="Stage labels render once a job-status endpoint exists."
          />
        </div>
      </div>
    </AppLayout>
  );
}

function AdminConsoleSection({ title, items, note }: { title: string; items: string[]; note: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <ul className="space-y-1.5 text-sm text-muted-foreground">
          {items.map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span className="size-1.5 rounded-full bg-muted-foreground/50" />
              {item}
            </li>
          ))}
        </ul>
        <p className="rounded-md border border-dashed border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {note} Actions stay disabled until their endpoints are implemented — the backend, not
          this interface, remains the authorization boundary.
        </p>
      </CardContent>
    </Card>
  );
}
