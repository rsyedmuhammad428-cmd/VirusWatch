import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { supabase } from "@/integrations/supabase/client";

/**
 * Managed auth gate for the whole protected subtree. Runs client-side only
 * (the session lives in browser storage), so SSR is disabled here.
 */
export const Route = createFileRoute("/_authenticated")({
  ssr: false,
  beforeLoad: async () => {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) {
      throw redirect({ to: "/auth" });
    }
  },
  component: () => <Outlet />,
});
