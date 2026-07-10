import type { EventAttemptItem, EventItem, EventLogItem } from "./event";

export type DeadLetterEventItem = {
  id: string;
  event_id: string;
  reason: string;
  error_message: string | null;
  retry_count: number;
  original_routing_key: string | null;
  created_at: string;
  correlation_id: string;
  trace_id: string;
  event_status: string;
};

export type DeadLetterEventDetail = {
  id: string;
  event_id: string;
  reason: string;
  payload: Record<string, unknown>;
  retry_count: number;
  original_routing_key: string | null;
  error_message: string | null;
  created_at: string;
  event: EventItem;
  attempts: EventAttemptItem[];
  logs: EventLogItem[];
};

export type DeadLetterReprocessResponse = {
  dead_letter_event_id: string;
  event_id: string;
  status: string;
  routing_key: string;
  correlation_id: string;
  trace_id: string;
};
