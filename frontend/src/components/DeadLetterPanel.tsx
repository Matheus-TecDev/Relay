import { Eye, RefreshCcw, RotateCcw, X } from "lucide-react";
import { useMemo, useState } from "react";

import {
  fetchDeadLetterEventDetail,
  reprocessDeadLetterEvent,
  reprocessDeadLetterEventsBatch
} from "../api/deadLetterEvents";
import type {
  DeadLetterBatchReprocessResponse,
  DeadLetterEventDetail,
  DeadLetterEventItem
} from "../types/deadLetterEvent";
import { CopyButton } from "./CopyButton";
import { JsonViewer } from "./JsonViewer";
import { StatusBadge } from "./StatusBadge";

const MAX_BATCH_SIZE = 20;
const REPROCESSABLE_STATUS = "dead_letter";

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
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [isBatchConfirmOpen, setIsBatchConfirmOpen] = useState(false);
  const [isBatchReprocessing, setIsBatchReprocessing] = useState(false);
  const [batchReport, setBatchReport] = useState<DeadLetterBatchReprocessResponse | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);

  // The list can change under the selection after a refresh, so only keep what is still eligible.
  const activeSelectedIds = useMemo(
    () =>
      selectedIds.filter((id) => events.some((event) => event.id === id && event.event_status === REPROCESSABLE_STATUS)),
    [events, selectedIds]
  );
  const selectedEventIds = useMemo(
    () => new Set(events.filter((event) => activeSelectedIds.includes(event.id)).map((event) => event.event_id)),
    [events, activeSelectedIds]
  );
  const isSelectionFull = activeSelectedIds.length >= MAX_BATCH_SIZE;
  // If the refresh after a batch fails the list is stale, so remember what that batch already queued.
  const queuedEventIds = useMemo(
    () =>
      new Set(
        (batchReport?.results ?? [])
          .filter((result) => result.status === "queued" && result.event_id !== null)
          .map((result) => result.event_id)
      ),
    [batchReport]
  );

  function selectionBlockReason(event: DeadLetterEventItem): string | null {
    if (event.event_status !== REPROCESSABLE_STATUS) {
      return `Event is ${event.event_status}, not in dead letter state`;
    }
    if (queuedEventIds.has(event.event_id)) {
      return "Already queued by the last batch";
    }
    if (activeSelectedIds.includes(event.id)) {
      return null;
    }
    if (selectedEventIds.has(event.event_id)) {
      return "Another dead letter entry for the same event is already selected";
    }
    if (isSelectionFull) {
      return `A batch is limited to ${MAX_BATCH_SIZE} events`;
    }
    return null;
  }

  function toggleSelection(event: DeadLetterEventItem) {
    if (activeSelectedIds.includes(event.id)) {
      setSelectedIds(activeSelectedIds.filter((id) => id !== event.id));
      return;
    }
    if (selectionBlockReason(event) === null) {
      setSelectedIds([...activeSelectedIds, event.id]);
    }
  }

  // One entry per event, up to the batch limit, keeping what is already selected.
  function selectAllEligible() {
    const nextIds = [...activeSelectedIds];
    const nextEventIds = new Set(selectedEventIds);
    for (const event of events) {
      if (nextIds.length >= MAX_BATCH_SIZE) {
        break;
      }
      if (
        event.event_status === REPROCESSABLE_STATUS &&
        !nextEventIds.has(event.event_id) &&
        !queuedEventIds.has(event.event_id)
      ) {
        nextIds.push(event.id);
        nextEventIds.add(event.event_id);
      }
    }
    setSelectedIds(nextIds);
  }

  const hasSelectableEvents = events.some(
    (event) => !activeSelectedIds.includes(event.id) && selectionBlockReason(event) === null
  );

  function confirmBatchReprocess() {
    const ids = activeSelectedIds;
    setIsBatchReprocessing(true);
    setActionError(null);
    setActionMessage(null);
    setBatchError(null);
    setBatchReport(null);
    reprocessDeadLetterEventsBatch(ids)
      .then((response) => {
        setBatchReport(response);
        setSelectedIds([]);
        return onRefresh();
      })
      .catch((reason: unknown) => {
        setBatchError(reason instanceof Error ? reason.message : "Failed to reprocess selected events");
      })
      .finally(() => {
        setIsBatchReprocessing(false);
        setIsBatchConfirmOpen(false);
      });
  }

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

      {activeSelectedIds.length > 0 ? (
        <div className="selection-bar" role="status">
          <span>
            {activeSelectedIds.length} of {MAX_BATCH_SIZE} selected
          </span>
          <div className="panel-actions">
            <button
              className="command-button"
              type="button"
              disabled={isBatchReprocessing}
              onClick={() => setSelectedIds([])}
            >
              Clear
            </button>
            <button
              className="command-button primary-command"
              type="button"
              disabled={isBatchReprocessing || isLoading}
              onClick={() => setIsBatchConfirmOpen(true)}
            >
              <RotateCcw size={16} />
              Reprocess selected
            </button>
          </div>
        </div>
      ) : null}

      {batchError ? (
        <div className="inline-alert inline-alert-error" role="alert">
          Batch reprocess failed: {batchError}. No result was received; refresh and check event statuses before retrying.
        </div>
      ) : null}

      {batchReport ? (
        <div className="batch-report" role="status">
          <div className="section-title-row">
            <h3>Batch reprocess result</h3>
            <button className="icon-button" type="button" title="Dismiss" onClick={() => setBatchReport(null)}>
              <X size={16} />
            </button>
          </div>
          <p>
            {batchReport.summary.queued} of {batchReport.summary.total} queued in the outbox
            {batchReport.summary.conflict > 0 ? ` · ${batchReport.summary.conflict} skipped` : ""}
            {batchReport.summary.not_found > 0 ? ` · ${batchReport.summary.not_found} not found` : ""}
            {batchReport.summary.failed > 0 ? ` · ${batchReport.summary.failed} failed` : ""}
          </p>
          <ul className="batch-report-list">
            {batchReport.results.map((result) => (
              <li key={result.dead_letter_event_id}>
                <span className={`status status-batch-${result.status}`}>{result.status}</span>
                <span className="mono-cell">{shortId(result.event_id ?? result.dead_letter_event_id)}</span>
                <span>{result.message ?? "Queued in the outbox; publication is asynchronous."}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {isLoading ? <div className="table-state">Loading dead letter events...</div> : null}
      {!isLoading && events.length === 0 ? <div className="table-state">No dead letter events found.</div> : null}

      {!isLoading && events.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="select-cell">
                  <input
                    type="checkbox"
                    aria-label="Select all eligible events"
                    title={`Select eligible events (up to ${MAX_BATCH_SIZE})`}
                    checked={activeSelectedIds.length > 0 && !hasSelectableEvents}
                    disabled={isBatchReprocessing || (activeSelectedIds.length === 0 && !hasSelectableEvents)}
                    onChange={() => (hasSelectableEvents ? selectAllEligible() : setSelectedIds([]))}
                  />
                </th>
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
                  <td className="select-cell">
                    <input
                      type="checkbox"
                      aria-label={`Select dead letter event ${shortId(event.id)}`}
                      title={selectionBlockReason(event) ?? "Select for batch reprocess"}
                      checked={activeSelectedIds.includes(event.id)}
                      disabled={isBatchReprocessing || selectionBlockReason(event) !== null}
                      onChange={() => toggleSelection(event)}
                    />
                  </td>
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
                        disabled={reprocessingId === event.id || isBatchReprocessing}
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

      {isBatchConfirmOpen ? (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-panel confirm-dialog" role="dialog" aria-modal="true" aria-label="Confirm batch reprocess">
            <div className="modal-header">
              <div>
                <span className="eyebrow">Confirm batch reprocess</span>
                <h2>
                  Reprocess {activeSelectedIds.length} dead letter {activeSelectedIds.length === 1 ? "event" : "events"}?
                </h2>
              </div>
            </div>
            <div className="confirm-body">
              <p>
                Each event is queued in the outbox independently and published asynchronously. Events that are no
                longer eligible are skipped and reported individually.
              </p>
              <div className="panel-actions">
                <button
                  className="command-button"
                  type="button"
                  disabled={isBatchReprocessing}
                  onClick={() => setIsBatchConfirmOpen(false)}
                >
                  Cancel
                </button>
                <button
                  className="command-button danger-action"
                  type="button"
                  disabled={isBatchReprocessing || activeSelectedIds.length === 0}
                  onClick={confirmBatchReprocess}
                >
                  <RotateCcw size={16} />
                  {isBatchReprocessing ? "Reprocessing..." : "Confirm reprocess"}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

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
