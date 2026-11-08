import {
  Activity,
  Bell,
  Bot,
  ClipboardList,
  Database,
  FileText,
  LayoutDashboard,
  MapPin,
  Settings,
  ShieldCheck,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  title: string;
  url: string;
  icon: LucideIcon;
  /** Administration is only shown to users holding an admin role. */
  adminOnly?: boolean;
}

export const navItems: NavItem[] = [
  { title: "Overview", url: "/dashboard", icon: LayoutDashboard },
  { title: "Surveillance", url: "/surveillance", icon: ClipboardList },
  { title: "Disease Explorer", url: "/diseases", icon: Activity },
  { title: "Geographic Explorer", url: "/geography", icon: MapPin },
  { title: "AI Research Assistant", url: "/assistant", icon: Bot },
  { title: "Trends & Anomalies", url: "/anomalies", icon: TrendingUp },
  { title: "Reports and Research", url: "/reports", icon: FileText },
  { title: "Notifications", url: "/notifications", icon: Bell },
  { title: "Data Sources", url: "/data-sources", icon: Database },
  { title: "Settings", url: "/settings", icon: Settings },
  { title: "Administration", url: "/admin", icon: ShieldCheck, adminOnly: true },
];

export function pageTitleFor(pathname: string): string {
  const exact = navItems.find((item) => item.url === pathname);
  if (exact) return exact.title;
  const partial = navItems.find((item) => pathname.startsWith(item.url));
  if (partial) return partial.title;
  return "VirusWatch";
}
