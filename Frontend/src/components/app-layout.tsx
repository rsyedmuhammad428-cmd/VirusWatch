import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-sidebar";
import { Topbar } from "@/components/topbar";
import { GlobalFiltersProvider } from "@/lib/filters";
import type { ReactNode } from "react";

export function AppLayout({ children }: { children: ReactNode }) {
  return (
    <GlobalFiltersProvider>
      <SidebarProvider>
        <div className="flex min-h-screen w-full">
          <AppSidebar />
          <SidebarInset className="flex min-w-0 flex-1 flex-col bg-background">
            <Topbar />
            <main className="min-w-0 flex-1 px-4 py-6 md:px-6 lg:px-8">{children}</main>
          </SidebarInset>
        </div>
      </SidebarProvider>
    </GlobalFiltersProvider>
  );
}
