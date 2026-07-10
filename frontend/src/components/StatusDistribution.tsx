import type { DashboardSummary, EventStatus } from "../types/event";

const statuses: EventStatus[] = [
  "received",
  "queued",
  "processing",
  "processed",
  "failed",
  "dead_letter",
  "publish_failed"
];

type StatusDistributionProps = {
  summary: DashboardSummary;
};

export function StatusDistribution({ summary }: StatusDistributionProps) {
  const total = Math.max(summary.total_events, 1);

  return (
    <section className="panel compact-panel">
      <div className="panel-header">
        <div>
          <h2>Status distribution</h2>
          <p>Current event state across the loaded operational window.</p>
        </div>
      </div>
      <div className="status-distribution">
        {statuses.map((status) => {
          const count = summary.by_status[status] ?? 0;
          const width = `${Math.max((count / total) * 100, count > 0 ? 4 : 0)}%`;
          return (
            <div className="status-row" key={status}>
              <div className="status-row-label">
                <span>{status}</span>
                <strong>{count}</strong>
              </div>
              <div className="status-bar" aria-hidden="true">
                <span className={`status-bar-fill status-fill-${status}`} style={{ width }} />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
