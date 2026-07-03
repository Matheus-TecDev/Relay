import type { LucideIcon } from "lucide-react";

type MetricCardProps = {
  label: string;
  value: string;
  tone: "blue" | "green" | "amber" | "red";
  icon: LucideIcon;
};

export function MetricCard({ label, value, tone, icon: Icon }: MetricCardProps) {
  return (
    <section className={`metric metric-${tone}`}>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <Icon size={22} aria-hidden="true" />
    </section>
  );
}

