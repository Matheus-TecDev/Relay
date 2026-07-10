export type EventStatus =
  | "received"
  | "queued"
  | "processing"
  | "processed"
  | "failed"
  | "dead_letter"
  | "publish_failed";

export type EventItem = {
  id: string;
  event_type: string;
  payload: Record<string, unknown>;
  routing_key: string | null;
  correlation_id: string;
  trace_id: string;
  status: EventStatus | string;
  created_at: string;
  updated_at: string;
};

export type EventAttemptItem = {
  id: string;
  event_id: string;
  attempt_number: number;
  status: string;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
};

export type EventLogItem = {
  id: string;
  event_id: string;
  level: string;
  message: string;
  log_metadata: Record<string, unknown>;
  created_at: string;
};

export type EventDeadLetterEntry = {
  id: string;
  event_id: string;
  reason: string;
  payload: Record<string, unknown>;
  retry_count: number;
  original_routing_key: string | null;
  error_message: string | null;
  created_at: string;
};

export type EventDetail = EventItem & {
  attempts: EventAttemptItem[];
  logs: EventLogItem[];
  dead_letter_entries: EventDeadLetterEntry[];
};

export type EventFilters = {
  status: string;
  routingKey: string;
  eventType: string;
  correlationId: string;
  traceId: string;
  search: string;
};

export type DashboardSummary = {
  total_events: number;
  by_status: Record<EventStatus | string, number>;
  dead_letter_total: number;
  oldest_dead_letter_age_seconds: number | null;
  recent_events_count: number;
};
