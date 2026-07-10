import { Activity, AlertTriangle, CheckCircle2, Clock3, Database, RadioTower, RefreshCcw, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { fetchEventsSummary } from "../api/events";
import type { DeadLetterEventItem } from "../types/deadLetterEvent";
import type { DashboardSummary as DashboardSummaryType, EventItem, EventStatus } from "../types/event";
import { MetricCard } from "./MetricCard";
import { StatusDistribution } from "./StatusDistribution";

const emptyByStatus: Record<EventStatus, number> = {
  received: 0,
  queued: 0,
  processing: 0,
  processed: 0,
  failed: 0,
  dead_letter: 0,
  publish_failed: 0
};

type DashboardSummaryProps = {
  events: EventItem[];
  deadLetterEvents: DeadLetterEventItem[];
  isLoading: boolean;
  refreshKey: number;
};

export function DashboardSummary({ events, deadLetterEvents, isLoading, refreshKey }: DashboardSummaryProps) {
  const [summary, setSummary] = useState<DashboardSummaryType | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    fetchEventsSummary()
      .then((data) => {
        if (isMounted) {
          setSummary(data);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (isMounted) {
          setError(reason instanceof Error ? reason.message : "Summary endpoint unavailable");
        }
      });

    return () => {
      isMounted = false;
    };
  }, [refreshKey]);

  const fallbackSummary = useMemo(
    () => buildFallbackSummary(events, deadLetterEvents),
    [deadLetterEvents, events]
  );
  const effectiveSummary = summary ?? fallbackSummary;
  const sourceLabel = summary ? "API summary" : "recent events fallback";

  return (
    <>
      <section className="dashboard-intro">
        <div>
          <span className="eyebrow">Operational dashboard</span>
          <h1>Event processing overview</h1>
          <p>
            Live view of recent events, processing state, DLQ pressure and operational signals from Relay.
          </p>
        </div>
        <div className="summary-source">
          <RadioTower size={16} />
          <span>{isLoading ? "Loading" : sourceLabel}</span>
        </div>
      </section>

      {error ? <div className="inline-alert inline-alert-warning">Using fallback data: {error}</div> : null}

      <section className="metrics-grid" aria-label="Operational metrics">
        <MetricCard label="Total events" value={formatNumber(effectiveSummary.total_events)} tone="blue" icon={Database} />
        <MetricCard label="Queued" value={formatNumber(effectiveSummary.by_status.queued ?? 0)} tone="blue" icon={Clock3} />
        <MetricCard
          label="Processing"
          value={formatNumber(effectiveSummary.by_status.processing ?? 0)}
          tone="amber"
          icon={RefreshCcw}
        />
        <MetricCard
          label="Processed"
          value={formatNumber(effectiveSummary.by_status.processed ?? 0)}
          tone="green"
          icon={CheckCircle2}
        />
        <MetricCard label="Failed" value={formatNumber(effectiveSummary.by_status.failed ?? 0)} tone="red" icon={XCircle} />
        <MetricCard
          label="Dead letter"
          value={formatNumber(effectiveSummary.by_status.dead_letter ?? 0)}
          tone="red"
          icon={AlertTriangle}
        />
        <MetricCard
          label="Publish failed"
          value={formatNumber(effectiveSummary.by_status.publish_failed ?? 0)}
          tone="red"
          icon={AlertTriangle}
        />
        <MetricCard
          label="DLQ total"
          value={formatNumber(effectiveSummary.dead_letter_total)}
          tone="amber"
          icon={Activity}
        />
      </section>

      <section className="dashboard-grid">
        <StatusDistribution summary={effectiveSummary} />
        <section className="panel compact-panel">
          <div className="panel-header">
            <div>
              <h2>DLQ age</h2>
              <p>Oldest dead letter event currently visible to the backend.</p>
            </div>
          </div>
          <div className="signal-list">
            <div>
              <span>Oldest DLQ event</span>
              <strong>{formatAge(effectiveSummary.oldest_dead_letter_age_seconds)}</strong>
            </div>
            <div>
              <span>Recent events window</span>
              <strong>{formatNumber(effectiveSummary.recent_events_count)}</strong>
            </div>
          </div>
        </section>
      </section>
    </>
  );
}

function buildFallbackSummary(events: EventItem[], deadLetterEvents: DeadLetterEventItem[]): DashboardSummaryType {
  const byStatus: Record<string, number> = { ...emptyByStatus };
  for (const event of events) {
    byStatus[event.status] = (byStatus[event.status] ?? 0) + 1;
  }

  return {
    total_events: events.length,
    by_status: byStatus,
    dead_letter_total: deadLetterEvents.length,
    oldest_dead_letter_age_seconds: oldestDeadLetterAge(deadLetterEvents),
    recent_events_count: events.length
  };
}

function oldestDeadLetterAge(events: DeadLetterEventItem[]): number | null {
  if (events.length === 0) {
    return null;
  }

  const oldest = events.reduce((currentOldest, event) => {
    const createdAt = new Date(event.created_at).getTime();
    return Math.min(currentOldest, createdAt);
  }, Number.POSITIVE_INFINITY);

  return Math.max((Date.now() - oldest) / 1000, 0);
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function formatAge(value: number | null): string {
  if (value === null) {
    return "No DLQ events";
  }

  if (value < 60) {
    return `${Math.round(value)}s`;
  }

  if (value < 3600) {
    return `${Math.round(value / 60)}m`;
  }

  return `${Math.round(value / 3600)}h`;
}
