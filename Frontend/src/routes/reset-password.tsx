import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { supabase } from "@/integrations/supabase/client";

export const Route = createFileRoute("/reset-password")({
  ssr: false,
  head: () => ({
    meta: [
      { title: "Reset password — VirusWatch" },
      { name: "description", content: "Set a new password for your VirusWatch account." },
      { property: "og:title", content: "Reset password — VirusWatch" },
      { property: "og:description", content: "Set a new password for your VirusWatch account." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ResetPasswordPage,
});

function ResetPasswordPage() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [invalidLink, setInvalidLink] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      toast.error("Passwords do not match.");
      return;
    }
    setBusy(true);
    // Recovery sessions are exempt from the current-password requirement.
    const { error } = await supabase.auth.updateUser({ password });
    setBusy(false);
    if (error) {
      setInvalidLink(
        error.message.includes("session") ||
          error.message.toLowerCase().includes("not authenticated"),
      );
      toast.error(error.message);
      return;
    }
    setDone(true);
    toast.success("Password updated. You can sign in with it now.");
    await navigate({ to: "/auth" });
  }

  if (invalidLink) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-4">
        <Card className="w-full max-w-sm text-center">
          <CardHeader>
            <CardTitle>Link expired</CardTitle>
            <CardDescription>
              This password-reset link is no longer valid. Request a new one from the sign-in page.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Set a new password</CardTitle>
          <CardDescription>Choose a strong password for your VirusWatch account.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="new-password">New password</Label>
              <Input
                id="new-password"
                type="password"
                required
                minLength={8}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="confirm-password">Confirm password</Label>
              <Input
                id="confirm-password"
                type="password"
                required
                minLength={8}
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </div>
            <Button type="submit" className="w-full" disabled={busy || done}>
              {busy ? <Loader2 className="animate-spin" /> : null}
              Update password
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
