import { useRouterState, useNavigate, useRouter } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ChevronDown, LogOut, RefreshCw, Settings, UserRound } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { FreshnessBadge } from "@/components/freshness-badge";
import { pageTitleFor } from "@/components/nav-config";
import { useBackendQuery, useBackendStatus } from "@/lib/api/hooks";
import { api } from "@/lib/api/endpoints";
import { supabase } from "@/integrations/supabase/client";

interface ProfileInfo {
  display_name: string | null;
  email: string | null;
}

function useProfile(): ProfileInfo | null {
  const [profile, setProfile] = useState<ProfileInfo | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user || cancelled) return;
      const { data } = await supabase
        .from("profiles")
        .select("display_name, email")
        .eq("id", user.id)
        .maybeSingle();
      if (!cancelled) {
        setProfile(
          data ?? { display_name: null, email: user.email ?? null },
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return profile;
}

export function Topbar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const title = pageTitleFor(pathname);
  const navigate = useNavigate();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { status } = useBackendStatus();
  const profile = useProfile();

  const freshness = useBackendQuery(["freshness"], (signal) => api.getFreshness(signal), status);

  const initials = (profile?.display_name || profile?.email || "U")
    .split(/[\s@._]/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");

  async function handleSignOut() {
    await queryClient.cancelQueries();
    queryClient.clear();
    await supabase.auth.signOut();
    await navigate({ to: "/auth", replace: true });
    router.invalidate();
  }

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-2.5 border-b border-border bg-card/80 px-3 backdrop-blur md:px-6">
      <SidebarTrigger
        aria-label="Toggle navigation menu"
        className="size-8 shrink-0 text-muted-foreground hover:text-foreground"
      />
      <h1 className="truncate font-display text-[15px] font-semibold tracking-tight text-foreground">
        {title}
      </h1>
      <div className="ml-auto flex items-center gap-2.5">
        <FreshnessBadge
          cadence={freshness.data?.reporting_cadence}
          lastRetrieval={freshness.data?.last_successful_retrieval}
          freshness={freshness.data?.freshness_status}
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="gap-2 px-1.5">
              <Avatar className="size-7">
                <AvatarFallback className="bg-primary/15 text-xs font-semibold text-primary">
                  {initials}
                </AvatarFallback>
              </Avatar>
              <span className="hidden max-w-[10rem] truncate text-sm text-foreground md:inline">
                {profile?.display_name || profile?.email || "Account"}
              </span>
              <ChevronDown className="size-3.5 text-muted-foreground" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="truncate">
              {profile?.email || "Signed in"}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => navigate({ to: "/settings" })}>
              <UserRound />
              Profile & settings
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={() => {
                freshness.refetch();
                toast.info("Re-checking backend availability…");
              }}
            >
              <RefreshCw />
              Check data connection
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-destructive focus:text-destructive"
              onClick={handleSignOut}
            >
              <LogOut />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

export function SettingsInlineIcon() {
  return <Settings className="size-4" />;
}
