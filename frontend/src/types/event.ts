export type EventItem = {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  routing_key: string | null;
  correlation_id: string;
  trace_id: string;
  status: string;
  created_at: string;
  updated_at: string;
};
