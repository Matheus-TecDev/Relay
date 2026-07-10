import type {
  DeadLetterEventDetail,
  DeadLetterEventItem,
  DeadLetterReprocessResponse
} from "../types/deadLetterEvent";
import { apiRequest } from "./client";

export async function fetchDeadLetterEvents(): Promise<DeadLetterEventItem[]> {
  return apiRequest<DeadLetterEventItem[]>("/api/dead-letter-events");
}

export async function fetchDeadLetterEventDetail(id: string): Promise<DeadLetterEventDetail> {
  return apiRequest<DeadLetterEventDetail>(`/api/dead-letter-events/${id}`);
}

export async function reprocessDeadLetterEvent(id: string): Promise<DeadLetterReprocessResponse> {
  return apiRequest<DeadLetterReprocessResponse>(`/api/dead-letter-events/${id}/reprocess`, {
    method: "POST"
  });
}
