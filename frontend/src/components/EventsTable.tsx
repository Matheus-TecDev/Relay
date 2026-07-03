import type { EventItem } from "../types/event";

type EventsTableProps = {
  events: EventItem[];
  isLoading: boolean;
};

function shortTraceId(traceId: string): string {
  return traceId.length > 12 ? `${traceId.slice(0, 12)}...` : traceId;
}

export function EventsTable({ events, isLoading }: EventsTableProps) {
  if (isLoading) {
    return <div className="table-state">Loading events...</div>;
  }

  if (events.length === 0) {
    return <div className="table-state">No events found.</div>;
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Status</th>
            <th>Routing</th>
            <th>Correlation</th>
            <th>Trace</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id}>
              <td>
                <strong>{event.event_type}</strong>
                <span>{event.id}</span>
              </td>
              <td>
                <span className={`status status-${event.status}`}>{event.status}</span>
              </td>
              <td>{event.routing_key ?? "default"}</td>
              <td className="mono-cell">{event.correlation_id}</td>
              <td className="mono-cell">{shortTraceId(event.trace_id)}</td>
              <td>{new Date(event.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
