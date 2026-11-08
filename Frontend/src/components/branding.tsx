import { ShieldCheck } from "lucide-react";

export function VirusWatchMark({ className }: { className?: string }) {
  return (
    <span
      className={`inline-flex size-7 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground ${className ?? ""}`}
      aria-hidden="true"
    >
      <ShieldCheck className="size-4.5" strokeWidth={2.2} />
    </span>
  );
}
