import { Eye, RefreshCcw, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import { fetchDeadLetterEventDetail, reprocessDeadLetterEvent } from "../api/deadLetterEvents";
import type { DeadLetterEventDetail, DeadLetterEventItem } from "../types/deadLetterEvent";
import { CopyButton } from "./CopyButton";
import { JsonViewer } from "./JsonViewer";
import { StatusBadge } from "./StatusBadge";

type DeadLetterPanelProps = {
  events: DeadLetterEventItem[];
  isLoading: boolean;
  error: string | null;
  onRefresh: () => Promise<void>;
};

export function DeadLetterPanel({ events, isLoading, error, onRefresh }: DeadLetterPanelProps) {
  const [selectedEvent, setSelectedEvent] = useState<DeadLetterEventDetail | null>(null);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [reprocessingId, setReprocessingId] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  function openDetail(id: string) {
    setIsDetailLoading(true);
    setActionError(null);
    fetchDeadLetterEventDetail(id)
      .then(setSelectedEvent)
      .catch((reason: unknown) => {
        setActionError(reason instanceof Error ? reason.message : "Failed to load detail");
      })
      .finally(() => setIsDetailLoading(false));
  }

  function requestReprocess(id: string) {
    if (confirmingId !== id) {
      setConfirmingId(id);
      setActionMessage("Click confirm to reprocess this dead letter event.");
      return;
    }

    setReprocessingId(id);
    setActionError(null);
    setActionMessage(null);
    reprocessDeadLetterEvent(id)
      .then((response) => {
        setActionMessage(`Reprocess queued for ${shortId(response.event_id)}`);
        return Promise.all([onRefresh(), fetchDeadLetterEventDetail(id)]);
      })
      .then(([, detail]) => setSelectedEvent(detail))
      .catch((reason: unknown) => {
        setActionError(reason instanceof Error ? reason.message : "Failed to reprocess event");
      })
      .finally(() => {
        setReprocessingId(null);
        setConfirmingId(null);
      });
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Dead Letter Queue</h2>
          <p>Failed events that exceeded retry limits and need operator action.</p>
        </div>
        <div className="panel-actions">
          {error ? <span className="error-text">{error}</span> : null}
          <button className="command-button" type="button" onClick={onRefresh} disabled={isLoading}>
            <RefreshCcw size={16} />
            Refresh
          </button>
        </div>
      </div>

      {isLoading ? <div className="table-state">Loading dead letter events...</div> : null}
      {!isLoading && events.length === 0 ? <div className="table-state">No dead letter events found.</div> : null}

      {!isLoading && events.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Reason</th>
                <th>Error</th>
                <th>Retries</th>
                <th>Routing</th>
                <th>Status</th>
                <th>Correlation</th>
                <th>Trace</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td>
                    <strong>{event.reason}</strong>
                    <span className="mono-cell">{shortId(event.event_id)}</span>
                  </td>
                  <td>{event.error_message ?? "No error message"}</td>
                  <td>{event.retry_count}</td>
                  <td>{event.original_routing_key ?? "unknown"}</td>
                  <td>
                    <StatusBadge status={event.event_status} />
                  </td>
                  <td className="mono-cell">{shortId(event.correlation_id)}</td>
                  <td className="mono-cell">{shortId(event.trace_id)}</td>
                  <td>{formatDate(event.created_at)}</td>
                  <td>
                    <div className="row-actions">
                      <button className="icon-button" type="button" title="View detail" onClick={() => openDetail(event.id)}>
                        <Eye size={16} />
                      </button>
                      <button
                        className={`icon-button ${confirmingId === event.id ? "danger-action" : ""}`}
                        type="button"
                        title={confirmingId === event.id ? "Confirm reprocess" : "Reprocess"}
                        disabled={reprocessingId === event.id}
                        onClick={() => requestReprocess(event.id)}
                      >
                        <RotateCcw size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {actionError ? <div className="inline-alert inline-alert-error">{actionError}</div> : null}
      {actionMessage ? <div className="inline-alert inline-alert-success">{actionMessage}</div> : null}

      {selectedEvent || isDetailLoading ? (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-panel" role="dialog" aria-modal="true" aria-label="Dead letter event detail">
            <div className="modal-header">
              <div>
                <span className="eyebrow">Dead letter detail</span>
                <h2>{selectedEvent?.reason ?? "Loading detail"}</h2>
                {selectedEvent ? <p>{selectedEvent.original_routing_key ?? "unknown routing key"}</p> : null}
              </div>
              <button className="icon-button" type="button" title="Close" onClick={() => setSelectedEvent(null)}>
                <X size={16} />
              </button>
            </div>

            {isDetailLoading ? <div className="table-state">Loading detail...</div> : null}

            {selectedEvent ? (
              <div className="detail-grid">
                <section>
                  <div className="section-title-row">
                    <h3>Payload</h3>
                    <StatusBadge status={selectedEvent.event.status} />
                  </div>
                  <JsonViewer value={selectedEvent.payload} />
                </section>
                <section>
                  <h3>Technical data</h3>
                  <dl className="detail-list">
                    <dt>Event ID</dt>
                    <dd className="copyable-value">
                      <span className="mono-cell">{selectedEvent.event_id}</span>
                      <CopyButton value={selectedEvent.event_id} label="event_id" />
                    </dd>
                    <dt>Correlation</dt>
                    <dd className="copyable-value">
                      <span className="mono-cell">{selectedEvent.event.correlation_id}</span>
                      <CopyButton value={selectedEvent.event.correlation_id} label="correlation_id" />
                    </dd>
                    <dt>Trace</dt>
                    <dd className="copyable-value">
                      <span className="mono-cell">{selectedEvent.event.trace_id}</span>
                      <CopyButton value={selectedEvent.event.trace_id} label="trace_id" />
                    </dd>
                    <dt>Retries</dt>
                    <dd>{selectedEvent.retry_count}</dd>
                    <dt>Error</dt>
                    <dd>{selectedEvent.error_message ?? "No error message"}</dd>
                    <dt>Created</dt>
                    <dd>{formatDate(selectedEvent.created_at)}</dd>
                  </dl>
                  <button
                    className={`command-button ${confirmingId === selectedEvent.id ? "danger-action" : ""}`}
                    type="button"
                    disabled={reprocessingId === selectedEvent.id}
                    onClick={() => requestReprocess(selectedEvent.id)}
                  >
                    <RotateCcw size={16} />
                    {confirmingId === selectedEvent.id ? "Confirm reprocess" : "Reprocess"}
                  </button>
                </section>
                <section>
                  <h3>Attempts</h3>
                  <div className="stack-list">
                    {selectedEvent.attempts.length === 0 ? <div className="empty-box">No attempts recorded.</div> : null}
                    {selectedEvent.attempts.map((attempt) => (
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
                    {selectedEvent.logs.length === 0 ? <div className="empty-box">No logs recorded.</div> : null}
                    {selectedEvent.logs.map((log) => (
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
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 12)}...` : value;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}
