import { Eye, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import { fetchDeadLetterEventDetail, reprocessDeadLetterEvent } from "../api/deadLetterEvents";
import type { DeadLetterEventDetail, DeadLetterEventItem } from "../types/deadLetterEvent";

type DeadLetterPanelProps = {
  events: DeadLetterEventItem[];
  isLoading: boolean;
  error: string | null;
  onRefresh: () => Promise<void>;
};

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 12)}...` : value;
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function DeadLetterPanel({ events, isLoading, error, onRefresh }: DeadLetterPanelProps) {
  const [selectedEvent, setSelectedEvent] = useState<DeadLetterEventDetail | null>(null);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [reprocessingId, setReprocessingId] = useState<string | null>(null);

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

  function reprocess(id: string) {
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
      .finally(() => setReprocessingId(null));
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Dead letter queue</h2>
          <p>Failed events that exceeded retry limits and need operator action.</p>
        </div>
        {error ? <span className="error-text">{error}</span> : null}
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
                    <span>{shortId(event.event_id)}</span>
                  </td>
                  <td>{event.error_message ?? "No error message"}</td>
                  <td>{event.retry_count}</td>
                  <td>{event.original_routing_key ?? "unknown"}</td>
                  <td className="mono-cell">{shortId(event.correlation_id)}</td>
                  <td className="mono-cell">{shortId(event.trace_id)}</td>
                  <td>{new Date(event.created_at).toLocaleString()}</td>
                  <td>
                    <div className="row-actions">
                      <button className="icon-button" type="button" title="View detail" onClick={() => openDetail(event.id)}>
                        <Eye size={16} />
                      </button>
                      <button
                        className="icon-button"
                        type="button"
                        title="Reprocess"
                        disabled={reprocessingId === event.id}
                        onClick={() => reprocess(event.id)}
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
                <h2>Dead letter detail</h2>
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
                  <h3>Payload</h3>
                  <pre>{formatJson(selectedEvent.payload)}</pre>
                </section>
                <section>
                  <h3>Technical data</h3>
                  <dl className="detail-list">
                    <dt>Status</dt>
                    <dd>{selectedEvent.event.status}</dd>
                    <dt>Correlation</dt>
                    <dd className="mono-cell">{selectedEvent.event.correlation_id}</dd>
                    <dt>Trace</dt>
                    <dd className="mono-cell">{selectedEvent.event.trace_id}</dd>
                    <dt>Error</dt>
                    <dd>{selectedEvent.error_message ?? "No error message"}</dd>
                  </dl>
                  <button
                    className="command-button"
                    type="button"
                    disabled={reprocessingId === selectedEvent.id}
                    onClick={() => reprocess(selectedEvent.id)}
                  >
                    <RotateCcw size={16} />
                    Reprocessar
                  </button>
                </section>
                <section>
                  <h3>Attempts</h3>
                  <div className="stack-list">
                    {selectedEvent.attempts.map((attempt) => (
                      <div className="stack-item" key={attempt.id}>
                        <strong>Attempt {attempt.attempt_number}</strong>
                        <span>{attempt.status}</span>
                        {attempt.error_message ? <small>{attempt.error_message}</small> : null}
                      </div>
                    ))}
                  </div>
                </section>
                <section>
                  <h3>Logs</h3>
                  <div className="stack-list">
                    {selectedEvent.logs.map((log) => (
                      <div className="stack-item" key={log.id}>
                        <strong>{log.message}</strong>
                        <span>{new Date(log.created_at).toLocaleString()}</span>
                        <small>{formatJson(log.log_metadata)}</small>
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
