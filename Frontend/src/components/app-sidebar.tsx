import { useEffect, useState } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";

import { VirusWatchMark } from "@/components/branding";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { navItems } from "@/components/nav-config";
import { supabase } from "@/integrations/supabase/client";

/** Reads the signed-in user's own roles (RLS scopes this read). */
function useAdminRole(): boolean {
  const { data } = useQuery({
    queryKey: ["my-roles"],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const { data: { user } } = await supabase.auth.getUser();
      if (!user) return [];
      const { data: roles, error } = await supabase
        .from("user_roles")
        .select("role")
        .eq("user_id", user.id);
      if (error) return [];
      return roles.map((r) => r.role as string);
    },
  });
  return !!data?.includes("admin");
}

export function AppSidebar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const isAdmin = useAdminRole();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const visibleItems = navItems.filter((item) => !item.adminOnly || (mounted && isAdmin));

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="lg" className="gap-2.5">
              <Link to="/dashboard">
                <VirusWatchMark className="size-7 shrink-0" />
                <span className="flex flex-col leading-tight">
                  <span className="font-display text-[15px] font-bold tracking-tight text-sidebar-foreground">
                    VirusWatch
                  </span>
                  <span className="text-[10px] uppercase tracking-widest text-sidebar-muted">
                    Health surveillance
                  </span>
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Monitoring</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {visibleItems.map((item) => (
                <SidebarMenuItem key={item.url}>
                  <SidebarMenuButton asChild isActive={pathname.startsWith(item.url)} tooltip={item.title}>
                    <Link to={item.url}>
                      <item.icon />
                      <span>{item.title}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      {mounted && isAdmin ? (
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={pathname.startsWith("/admin")}>
                <Link to="/admin">
                  <ShieldCheck />
                  <span>Administration</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      ) : null}
    </Sidebar>
  );
}
