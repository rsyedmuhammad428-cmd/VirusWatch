import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { AtSign, KeyRound, Loader2, UserRound } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VirusWatchMark } from "@/components/branding";
import { supabase } from "@/integrations/supabase/client";
import { lovable } from "@/integrations/lovable";
import "@lovable.dev/cloud-auth-js/styles.css";

export const Route = createFileRoute("/auth")({
  ssr: false,
  beforeLoad: async () => {
    const { data: { user } } = await supabase.auth.getUser();
    if (user) throw redirect({ to: "/dashboard" });
  },
  head: () => ({
    meta: [
      { title: "Sign in — VirusWatch" },
      { name: "description", content: "Sign in to the VirusWatch public-health surveillance dashboard." },
      { property: "og:title", content: "Sign in — VirusWatch" },
      { property: "og:description", content: "Sign in to the VirusWatch public-health surveillance dashboard." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: AuthPage,
});

function AuthPage() {
  const navigate = useNavigate();
  const [signInEmail, setSignInEmail] = useState("");
  const [signInPassword, setSignInPassword] = useState("");
  const [signInBusy, setSignInBusy] = useState(false);
  const [signUpEmail, setSignUpEmail] = useState("");
  const [signUpPassword, setSignUpPassword] = useState("");
  const [signUpName, setSignUpName] = useState("");
  const [signUpBusy, setSignUpBusy] = useState(false);
  const [confirmationSent, setConfirmationSent] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);

  async function handleSignIn(event: FormEvent) {
    event.preventDefault();
    setSignInBusy(true);
    const { error } = await supabase.auth.signInWithPassword({
      email: signInEmail,
      password: signInPassword,
    });
    setSignInBusy(false);
    if (error) {
      toast.error(error.message);
      return;
    }
    await navigate({ to: "/dashboard" });
  }

  async function handleSignUp(event: FormEvent) {
    event.preventDefault();
    setSignUpBusy(true);
    const { error } = await supabase.auth.signUp({
      email: signUpEmail,
      password: signUpPassword,
      options: {
        emailRedirectTo: window.location.origin,
        ...(signUpName ? { data: { display_name: signUpName } } : {}),
      },
    });
    setSignUpBusy(false);
    if (error) {
      toast.error(error.message);
      return;
    }
    // With email confirmation on, no session is returned until the user
    // clicks the link in their inbox.
    setConfirmationSent(true);
  }

  async function handleResetEmail() {
    if (!signInEmail) {
      toast.error("Enter your email above first, then tap 'Forgot password?'.");
      return;
    }
    setResetBusy(true);
    const { error } = await supabase.auth.resetPasswordForEmail(signInEmail, {
      redirectTo: `${window.location.origin}/reset-password`,
    });
    setResetBusy(false);
    if (error) {
      toast.error(error.message);
      return;
    }
    toast.success("Password reset email sent — check your inbox.");
  }

  async function handleOAuth(provider: "google" | "lovable") {
    const result = await lovable.auth.signInWithOAuth(provider, {
      redirect_uri: window.location.origin,
    });
    if (result.error) {
      toast.error(result.error.message ?? "Sign-in failed. Try again.");
      return;
    }
    if (result.redirected) return;
    await navigate({ to: "/dashboard" });
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      {/* Brand panel */}
      <div className="relative hidden flex-col justify-between bg-sidebar p-10 text-sidebar-foreground lg:flex">
        <div className="flex items-center gap-2.5">
          <VirusWatchMark />
          <span className="font-display text-lg font-bold tracking-tight">VirusWatch</span>
        </div>
        <div className="max-w-md space-y-4">
          <h2 className="font-display text-3xl font-bold leading-tight text-sidebar-foreground">
            Infectious-disease surveillance, grounded in sources.
          </h2>
          <p className="text-sm leading-relaxed text-sidebar-muted">
            Explore validated surveillance observations, statistical anomalies, official
            guidance and research reports — with every figure traceable to the source that
            published it. Initial focus: Karachi, Sindh and Pakistan.
          </p>
          <p className="text-xs text-sidebar-muted">
            Surveillance data follows official reporting cadences. Weekly data is never
            presented as real-time.
          </p>
        </div>
        <p className="text-xs text-sidebar-muted">Public-health intelligence · Evidence-based</p>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center bg-background px-4 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <VirusWatchMark />
            <span className="font-display text-lg font-bold tracking-tight">VirusWatch</span>
          </div>

          {confirmationSent ? (
            <div className="space-y-4 rounded-xl border border-border bg-card p-6 text-center">
              <h2 className="font-display text-lg font-semibold">Check your email</h2>
              <p className="text-sm text-muted-foreground">
                We sent a confirmation link to <span className="font-medium">{signUpEmail}</span>.
                Click it to activate your account, then sign in here.
              </p>
              <Button variant="outline" size="sm" onClick={() => setConfirmationSent(false)}>
                Back to sign in
              </Button>
            </div>
          ) : (
            <Tabs defaultValue="sign-in">
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="sign-in">Sign in</TabsTrigger>
                <TabsTrigger value="sign-up">Create account</TabsTrigger>
              </TabsList>

              <TabsContent value="sign-in" className="mt-6 space-y-4">
                <form onSubmit={handleSignIn} className="space-y-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="signin-email">Email</Label>
                    <div className="relative">
                      <AtSign className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
                      <Input
                        id="signin-email"
                        type="email"
                        required
                        autoComplete="email"
                        className="pl-8"
                        value={signInEmail}
                        onChange={(e) => setSignInEmail(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="signin-password">Password</Label>
                    <div className="relative">
                      <KeyRound className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
                      <Input
                        id="signin-password"
                        type="password"
                        required
                        autoComplete="current-password"
                        className="pl-8"
                        value={signInPassword}
                        onChange={(e) => setSignInPassword(e.target.value)}
                      />
                    </div>
                  </div>
                  <Button type="submit" className="w-full" disabled={signInBusy}>
                    {signInBusy ? <Loader2 className="animate-spin" /> : null}
                    Sign in
                  </Button>
                  <button
                    type="button"
                    onClick={handleResetEmail}
                    disabled={resetBusy}
                    className="mx-auto block text-xs font-medium text-muted-foreground underline-offset-4 hover:underline"
                  >
                    Forgot password?
                  </button>
                </form>
              </TabsContent>

              <TabsContent value="sign-up" className="mt-6 space-y-4">
                <form onSubmit={handleSignUp} className="space-y-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="signup-name">Display name</Label>
                    <div className="relative">
                      <UserRound className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
                      <Input
                        id="signup-name"
                        className="pl-8"
                        placeholder="Optional"
                        autoComplete="name"
                        value={signUpName}
                        onChange={(e) => setSignUpName(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="signup-email">Email</Label>
                    <div className="relative">
                      <AtSign className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
                      <Input
                        id="signup-email"
                        type="email"
                        required
                        autoComplete="email"
                        className="pl-8"
                        value={signUpEmail}
                        onChange={(e) => setSignUpEmail(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="signup-password">Password</Label>
                    <div className="relative">
                      <KeyRound className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
                      <Input
                        id="signup-password"
                        type="password"
                        required
                        minLength={8}
                        autoComplete="new-password"
                        className="pl-8"
                        value={signUpPassword}
                        onChange={(e) => setSignUpPassword(e.target.value)}
                      />
                    </div>
                  </div>
                  <Button type="submit" className="w-full" disabled={signUpBusy}>
                    {signUpBusy ? <Loader2 className="animate-spin" /> : null}
                    Create account
                  </Button>
                  <p className="text-center text-xs text-muted-foreground">
                    You'll receive a confirmation email before signing in.
                  </p>
                </form>
              </TabsContent>
            </Tabs>
          )}

          {!confirmationSent ? (
            <>
              <div className="my-6 flex items-center gap-3">
                <span className="h-px flex-1 bg-border" />
                <span className="text-xs uppercase tracking-wide text-muted-foreground">or</span>
                <span className="h-px flex-1 bg-border" />
              </div>
              <div className="space-y-2.5">
                <button
                  type="button"
                  onClick={() => handleOAuth("google")}
                  className="flex w-full items-center justify-center gap-2.5 rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
                >
                  <GoogleGlyph />
                  Continue with Google
                </button>
                <button
                  type="button"
                  onClick={() => handleOAuth("lovable")}
                  className="lovable-auth-button w-full"
                >
                  Continue with Lovable
                </button>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function GoogleGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15A11 11 0 0 0 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
      />
    </svg>
  );
}
