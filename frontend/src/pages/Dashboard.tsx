import { Activity, CheckCircle2, Clock3, TriangleAlert } from "lucide-react";

import { EventsTable } from "../components/EventsTable";
import { MetricCard } from "../components/MetricCard";
import { useEvents } from "../hooks/useEvents";

const metrics = [
  { label: "Queued Events", value: "128", tone: "blue" as const, icon: Clock3 },
  { label: "Processed Events", value: "8.4k", tone: "green" as const, icon: CheckCircle2 },
  { label: "Failed Events", value: "12", tone: "red" as const, icon: TriangleAlert },
  { label: "Throughput/min", value: "342", tone: "amber" as const, icon: Activity }
];

export function Dashboard() {
  const { events, isLoading, error } = useEvents();

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">Relay</span>
          <h1>Event routing operations</h1>
        </div>
        <div className="env-pill">Development</div>
      </header>

      <section className="metrics-grid" aria-label="Metrics">
        {metrics.map((metric) => (
          <MetricCard key={metric.label} {...metric} />
        ))}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Recent events</h2>
            <p>Exchange-routed events with correlation and trace context.</p>
          </div>
          {error ? <span className="error-text">{error}</span> : null}
        </div>
        <EventsTable events={events} isLoading={isLoading} />
      </section>
    </main>
  );
}
