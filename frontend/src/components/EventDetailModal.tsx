import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { fetchEventDetail } from "../api/events";
import type { EventDetail } from "../types/event";
import { CopyButton } from "./CopyButton";
import { JsonViewer } from "./JsonViewer";
import { StatusBadge } from "./StatusBadge";

type EventDetailModalProps = {
  eventId: string;
  onClose: () => void;
};

export function EventDetailModal({ eventId, onClose }: EventDetailModalProps) {
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);
    setError(null);

    fetchEventDetail(eventId)
      .then((data) => {
        if (isMounted) {
          setEvent(data);
        }
      })
      .catch((reason: unknown) => {
        if (isMounted) {
          setError(reason instanceof Error ? reason.message : "Failed to load event detail");
        }
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, [eventId]);

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-panel" role="dialog" aria-modal="true" aria-label="Event detail">
        <div className="modal-header">
          <div>
            <span className="eyebrow">Event detail</span>
            <h2>{event?.event_type ?? "Loading event"}</h2>
            {event ? <p>{event.routing_key ?? "default routing key"}</p> : null}
          </div>
          <button className="icon-button" type="button" title="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        {isLoading ? <div className="table-state">Loading event detail...</div> : null}
        {error ? <div className="inline-alert inline-alert-error">{error}</div> : null}

        {event ? (
          <div className="detail-grid">
            <section>
              <div className="section-title-row">
                <h3>Payload</h3>
                <StatusBadge status={event.status} />
              </div>
              <JsonViewer value={event.payload} />
            </section>

            <section>
              <h3>Technical data</h3>
              <dl className="detail-list">
                <dt>Event ID</dt>
                <dd className="copyable-value">
                  <span className="mono-cell">{event.id}</span>
                  <CopyButton value={event.id} label="event_id" />
                </dd>
                <dt>Correlation</dt>
                <dd className="copyable-value">
                  <span className="mono-cell">{event.correlation_id}</span>
                  <CopyButton value={event.correlation_id} label="correlation_id" />
                </dd>
                <dt>Trace</dt>
                <dd className="copyable-value">
                  <span className="mono-cell">{event.trace_id}</span>
                  <CopyButton value={event.trace_id} label="trace_id" />
                </dd>
                <dt>Created</dt>
                <dd>{formatDate(event.created_at)}</dd>
                <dt>Updated</dt>
                <dd>{formatDate(event.updated_at)}</dd>
              </dl>
            </section>

            <section>
              <h3>Attempts</h3>
              <div className="stack-list">
                {event.attempts.length === 0 ? <div className="empty-box">No attempts recorded.</div> : null}
                {event.attempts.map((attempt) => (
                  <div className="stack-item" key={attempt.id}>
                    <strong>Attempt {attempt.attempt_number}</strong>
                    <span>{attempt.status}</span>
                    <small>{attempt.error_message ?? "No error message"}</small>
                  </div>
                ))}
              </div>
            </section>

            <section>
              <h3>Logs</h3>
              <div className="stack-list">
                {event.logs.length === 0 ? <div className="empty-box">No logs recorded.</div> : null}
                {event.logs.map((log) => (
                  <div className="stack-item" key={log.id}>
                    <strong>{log.message}</strong>
                    <span>
                      {log.level} · {formatDate(log.created_at)}
                    </span>
                    <small>{JSON.stringify(log.log_metadata, null, 2)}</small>
                  </div>
                ))}
              </div>
            </section>

            <section className="detail-wide">
              <h3>Dead letter entries</h3>
              <div className="stack-list">
                {event.dead_letter_entries.length === 0 ? <div className="empty-box">No DLQ entry for this event.</div> : null}
                {event.dead_letter_entries.map((entry) => (
                  <div className="stack-item" key={entry.id}>
                    <strong>{entry.reason}</strong>
                    <span>
                      {entry.original_routing_key ?? "unknown routing"} · retries: {entry.retry_count}
                    </span>
                    <small>{entry.error_message ?? "No error message"}</small>
                  </div>
                ))}
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}
