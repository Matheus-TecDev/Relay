import { Eye, RefreshCcw, Search } from "lucide-react";
import { useMemo, useState } from "react";

import type { EventFilters, EventItem } from "../types/event";
import { EventDetailModal } from "./EventDetailModal";
import { Pagination } from "./Pagination";
import { StatusBadge } from "./StatusBadge";

type EventsExplorerProps = {
  events: EventItem[];
  isLoading: boolean;
  error: string | null;
  onRefresh: () => Promise<void>;
};

const initialFilters: EventFilters = {
  status: "",
  routingKey: "",
  eventType: "",
  correlationId: "",
  traceId: "",
  search: ""
};

const pageSize = 10;

export function EventsExplorer({ events, isLoading, error, onRefresh }: EventsExplorerProps) {
  const [filters, setFilters] = useState<EventFilters>(initialFilters);
  const [page, setPage] = useState(1);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);

  const filterOptions = useMemo(() => buildFilterOptions(events), [events]);
  const filteredEvents = useMemo(() => filterEvents(events, filters), [events, filters]);
  const totalPages = Math.max(Math.ceil(filteredEvents.length / pageSize), 1);
  const currentPage = Math.min(page, totalPages);
  const paginatedEvents = filteredEvents.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  function updateFilter(key: keyof EventFilters, value: string) {
    setFilters((current) => ({ ...current, [key]: value }));
    setPage(1);
  }

  function resetFilters() {
    setFilters(initialFilters);
    setPage(1);
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Events</h2>
          <p>Recent events with filtering, pagination and detail inspection.</p>
        </div>
        <div className="panel-actions">
          {error ? <span className="error-text">{error}</span> : null}
          <button className="command-button" type="button" onClick={onRefresh} disabled={isLoading}>
            <RefreshCcw size={16} />
            Refresh
          </button>
        </div>
      </div>

      <div className="filters-grid">
        <label>
          <span>Status</span>
          <select value={filters.status} onChange={(event) => updateFilter("status", event.target.value)}>
            <option value="">All statuses</option>
            {filterOptions.statuses.map((status) => (
              <option value={status} key={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Routing key</span>
          <select value={filters.routingKey} onChange={(event) => updateFilter("routingKey", event.target.value)}>
            <option value="">All routing keys</option>
            {filterOptions.routingKeys.map((routingKey) => (
              <option value={routingKey} key={routingKey}>
                {routingKey}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Event type</span>
          <select value={filters.eventType} onChange={(event) => updateFilter("eventType", event.target.value)}>
            <option value="">All event types</option>
            {filterOptions.eventTypes.map((eventType) => (
              <option value={eventType} key={eventType}>
                {eventType}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Correlation ID</span>
          <input
            value={filters.correlationId}
            onChange={(event) => updateFilter("correlationId", event.target.value)}
            placeholder="correlation_id"
          />
        </label>
        <label>
          <span>Trace ID</span>
          <input value={filters.traceId} onChange={(event) => updateFilter("traceId", event.target.value)} placeholder="trace_id" />
        </label>
        <label>
          <span>Search</span>
          <div className="search-input">
            <Search size={15} />
            <input value={filters.search} onChange={(event) => updateFilter("search", event.target.value)} placeholder="event text" />
          </div>
        </label>
      </div>

      <div className="filter-actions">
        <span>{filteredEvents.length} events in current result set</span>
        <button type="button" onClick={resetFilters}>
          Clear filters
        </button>
      </div>

      {isLoading ? <div className="table-state">Loading events...</div> : null}
      {!isLoading && filteredEvents.length === 0 ? <div className="table-state">No events match the current filters.</div> : null}

      {!isLoading && filteredEvents.length > 0 ? (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Status</th>
                  <th>Routing</th>
                  <th>Correlation</th>
                  <th>Trace</th>
                  <th>Created</th>
                  <th>Updated</th>
                  <th>Attempts</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {paginatedEvents.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <strong>{event.event_type}</strong>
                      <span className="mono-cell">{shortId(event.id)}</span>
                    </td>
                    <td>
                      <StatusBadge status={event.status} />
                    </td>
                    <td>{event.routing_key ?? "default"}</td>
                    <td className="mono-cell">{shortId(event.correlation_id)}</td>
                    <td className="mono-cell">{shortId(event.trace_id)}</td>
                    <td>{formatDate(event.created_at)}</td>
                    <td>{formatDate(event.updated_at)}</td>
                    <td>N/A</td>
                    <td>
                      <button className="icon-button" type="button" title="View detail" onClick={() => setSelectedEventId(event.id)}>
                        <Eye size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={currentPage} pageSize={pageSize} totalItems={filteredEvents.length} onPageChange={setPage} />
        </>
      ) : null}

      {selectedEventId ? <EventDetailModal eventId={selectedEventId} onClose={() => setSelectedEventId(null)} /> : null}
    </section>
  );
}

function filterEvents(events: EventItem[], filters: EventFilters): EventItem[] {
  const search = filters.search.trim().toLowerCase();
  const correlationId = filters.correlationId.trim().toLowerCase();
  const traceId = filters.traceId.trim().toLowerCase();

  return [...events]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .filter((event) => {
      if (filters.status && event.status !== filters.status) {
        return false;
      }
      if (filters.routingKey && event.routing_key !== filters.routingKey) {
        return false;
      }
      if (filters.eventType && event.event_type !== filters.eventType) {
        return false;
      }
      if (correlationId && !event.correlation_id.toLowerCase().includes(correlationId)) {
        return false;
      }
      if (traceId && !event.trace_id.toLowerCase().includes(traceId)) {
        return false;
      }
      if (!search) {
        return true;
      }

      return [event.id, event.event_type, event.routing_key ?? "", event.status, event.correlation_id, event.trace_id]
        .join(" ")
        .toLowerCase()
        .includes(search);
    });
}

function buildFilterOptions(events: EventItem[]) {
  return {
    statuses: unique(events.map((event) => event.status)),
    routingKeys: unique(events.map((event) => event.routing_key).filter((value): value is string => Boolean(value))),
    eventTypes: unique(events.map((event) => event.event_type))
  };
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b));
}

function shortId(value: string): string {
  return value.length > 12 ? `${value.slice(0, 12)}...` : value;
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}
