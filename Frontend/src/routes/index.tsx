import { createFileRoute, Link } from "@tanstack/react-router";
import { Activity, ArrowRight, Bot, Database, FileSearch, MapPin, ShieldCheck, TrendingUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { VirusWatchMark } from "@/components/branding";
import landingDashboard from "@/assets/landing-dashboard.png";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "VirusWatch — Public Health Surveillance" },
      {
        name: "description",
        content:
          "Monitor infectious-disease surveillance data, validated anomalies, official guidance and research — grounded in sources. Initial focus: Karachi, Sindh and Pakistan.",
      },
      { property: "og:title", content: "VirusWatch — Public Health Surveillance" },
      {
        property: "og:description",
        content:
          "Monitor infectious-disease surveillance data, validated anomalies, official guidance and research — grounded in sources.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: LandingPage,
});

const capabilities = [
  {
    icon: Database,
    title: "Surveillance explorer",
    text: "Browse structured observations with period, unit, provenance and quality status.",
  },
  {
    icon: TrendingUp,
    title: "Validated anomalies",
    text: "Statistically detected unusual changes — clearly labelled, never overstated.",
  },
  {
    icon: Bot,
    title: "AI research assistant",
    text: "Evidence-based answers with expandable citations to the original sources.",
  },
  {
    icon: MapPin,
    title: "Geographic context",
    text: "City, provincial and national data clearly distinguished — never conflated.",
  },
  {
    icon: FileSearch,
    title: "Reports and research",
    text: "Official bulletins and publications with scope, period and source links.",
  },
  {
    icon: ShieldCheck,
    title: "Source transparency",
    text: "Every figure carries its organization, publication date and retrieval time.",
  },
];

function LandingPage() {
  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2.5">
          <VirusWatchMark className="bg-primary text-primary-foreground" />
          <span className="font-display text-lg font-bold tracking-tight text-foreground">
            VirusWatch
          </span>
        </div>
        <Button asChild size="sm">
          <Link to="/auth">
            Sign in
            <ArrowRight />
          </Link>
        </Button>
      </header>

      {/* Hero */}
      <section className="mx-auto grid max-w-6xl items-center gap-10 px-6 pb-16 pt-8 lg:grid-cols-2 lg:pt-14">
        <div className="space-y-6">
          <Badge variant="outline" className="gap-1.5 border-primary/30 bg-primary/5 text-primary">
            <Activity className="size-3" />
            Public-health surveillance intelligence
          </Badge>
          <h1 className="font-display text-4xl font-bold leading-[1.1] tracking-tight text-foreground sm:text-5xl">
            Infectious-disease surveillance, grounded in sources.
          </h1>
          <p className="max-w-lg text-base leading-relaxed text-muted-foreground">
            VirusWatch helps you monitor surveillance data, explore trends, identify unusual
            changes, review official guidance, and receive evidence-based public-health
            updates — every figure traceable to the source that published it.
          </p>
          <div className="flex flex-wrap gap-3">
            <Button asChild size="lg">
              <Link to="/auth">
                Open the dashboard
                <ArrowRight />
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link to="/auth">Create an account</Link>
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Initial focus: Karachi, Sindh and Pakistan, with support for the United States and
            additional regions as compatible data becomes available.
          </p>
        </div>
        <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
          <img
            src={landingDashboard}
            alt="Illustration of the VirusWatch surveillance dashboard"
            width={1200}
            height={800}
            className="h-auto w-full"
          />
        </div>
      </section>

      {/* Capabilities */}
      <section className="border-t border-border bg-card/50 py-16">
        <div className="mx-auto max-w-6xl px-6">
          <h2 className="font-display text-2xl font-semibold tracking-tight text-foreground">
            What's inside
          </h2>
          <div className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {capabilities.map((cap) => (
              <div
                key={cap.title}
                className="rounded-xl border border-border bg-card p-5 transition-colors hover:border-primary/40"
              >
                <cap.icon className="size-5 text-primary" />
                <h3 className="mt-3 text-sm font-semibold text-foreground">{cap.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{cap.text}</p>
              </div>
            ))}
          </div>
          <p className="mt-10 max-w-3xl text-sm leading-relaxed text-muted-foreground">
            A note on rigor: VirusWatch only displays data served by its connected surveillance
            backend. Statistical anomalies are clearly distinguished from officially confirmed
            outbreaks, and weekly data is never presented as real-time.
          </p>
        </div>
      </section>

      <footer className="border-t border-border py-8">
        <p className="text-center text-xs text-muted-foreground">
          VirusWatch · Public-health intelligence for research and demonstration
        </p>
      </footer>
    </div>
  );
}
