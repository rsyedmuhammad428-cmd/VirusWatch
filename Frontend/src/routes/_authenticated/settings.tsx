import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Loader2, Plug, RefreshCw, Save } from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { endpoints } from "@/lib/api/endpoints";
import { useBackendStatus } from "@/lib/api/hooks";
import { supabase } from "@/integrations/supabase/client";

export const Route = createFileRoute("/_authenticated/settings")({
  head: () => ({
    meta: [
      { title: "Settings — VirusWatch" },
      { name: "description", content: "Profile, backend connection and integration settings." },
      { property: "og:title", content: "Settings — VirusWatch" },
      { property: "og:description", content: "Profile, backend connection and integration settings." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: SettingsPage,
});

function SettingsPage() {
  const { baseUrl, status, recheck, setBaseUrl } = useBackendStatus();
  const [urlDraft, setUrlDraft] = useState(baseUrl);
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [profileBusy, setProfileBusy] = useState(false);

  useEffect(() => {
    setUrlDraft(baseUrl);
  }, [baseUrl]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user || cancelled) return;
      setEmail(user.email ?? "");
      const { data } = await supabase
        .from("profiles")
        .select("display_name")
        .eq("id", user.id)
        .maybeSingle();
      if (!cancelled) setDisplayName(data?.display_name ?? "");
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    setProfileBusy(true);
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      setProfileBusy(false);
      return;
    }
    const { error } = await supabase
      .from("profiles")
      .update({ display_name: displayName, updated_at: new Date().toISOString() })
      .eq("id", user.id);
    setProfileBusy(false);
    if (error) {
      toast.error(error.message);
      return;
    }
    toast.success("Profile updated");
  }

  const statusBadge =
    status === "ready"
      ? { label: "Connected", tone: "border-positive/30 bg-positive/10 text-positive-foreground" }
      : status === "unreachable"
        ? { label: "Unreachable", tone: "border-destructive/40 bg-destructive/10 text-destructive" }
        : status === "checking"
          ? { label: "Checking…", tone: "border-border bg-muted text-muted-foreground" }
          : { label: "Not configured", tone: "border-border bg-muted text-muted-foreground" };

  return (
    <AppLayout>
      <div className="grid gap-5 lg:grid-cols-2">
        {/* Profile */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Profile</CardTitle>
            <CardDescription>How your name appears in the app.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={saveProfile} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="display-name">Display name</Label>
                <Input
                  id="display-name"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="Your name"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="account-email">Email</Label>
                <Input id="account-email" value={email} disabled />
                <p className="text-xs text-muted-foreground">Account email is managed at sign-in.</p>
              </div>
              <Button type="submit" size="sm" disabled={profileBusy}>
                {profileBusy ? <Loader2 className="animate-spin" /> : <Save />}
                Save profile
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* Backend connection */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Plug className="size-4 text-primary" />
              Surveillance backend
              <Badge variant="outline" className={statusBadge.tone}>
                {statusBadge.label}
              </Badge>
            </CardTitle>
            <CardDescription>
              The FastAPI service that supplies all surveillance data, analysis, AI answers and
              notifications. VirusWatch never displays data from anywhere else.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="api-url">API base URL</Label>
              <Input
                id="api-url"
                placeholder="https://your-backend.example.com"
                value={urlDraft}
                onChange={(e) => setUrlDraft(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Also settable at build time via the VITE_API_BASE_URL environment variable.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => {
                  setBaseUrl(urlDraft);
                  toast.success("Endpoint saved — checking availability…");
                }}
              >
                Save endpoint
              </Button>
              <Button size="sm" variant="outline" onClick={recheck}>
                <RefreshCw />
                Test connection
              </Button>
            </div>
            <div className="rounded-lg border border-border bg-muted/40 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Expected endpoints
              </p>
              <ul className="tnum mt-1.5 space-y-0.5 text-xs text-muted-foreground">
                {[
                  endpoints.summary,
                  endpoints.freshness,
                  endpoints.activity,
                  endpoints.diseases,
                  endpoints.observations,
                  endpoints.anomalies,
                  endpoints.reports,
                  endpoints.notifications,
                  endpoints.sources,
                  endpoints.aiAsk,
                ].map((path) => (
                  <li key={path}>
                    GET/POST <span className="text-foreground/80">{path}</span>
                  </li>
                ))}
              </ul>
            </div>
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
