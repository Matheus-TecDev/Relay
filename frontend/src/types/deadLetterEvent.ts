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

export type DeadLetterBatchReprocessItemStatus = "queued" | "conflict" | "not_found" | "failed";

export type DeadLetterBatchReprocessItem = {
  dead_letter_event_id: string;
  status: DeadLetterBatchReprocessItemStatus;
  reason: string | null;
  message: string | null;
  event_id: string | null;
  routing_key: string | null;
  correlation_id: string | null;
  trace_id: string | null;
};

export type DeadLetterBatchReprocessSummary = {
  total: number;
  queued: number;
  conflict: number;
  not_found: number;
  failed: number;
};

export type DeadLetterBatchReprocessResponse = {
  summary: DeadLetterBatchReprocessSummary;
  results: DeadLetterBatchReprocessItem[];
};
