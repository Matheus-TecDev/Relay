import type {
  DeadLetterEventDetail,
  DeadLetterEventItem,
  DeadLetterReprocessResponse
} from "../types/deadLetterEvent";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

async function parseResponse<T>(response: Response, fallbackMessage: string): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = typeof payload?.detail === "string" ? payload.detail : fallbackMessage;
    throw new Error(detail);
  }

  return response.json();
}

export async function fetchDeadLetterEvents(): Promise<DeadLetterEventItem[]> {
  const response = await fetch(`${API_BASE_URL}/api/dead-letter-events`);
  return parseResponse<DeadLetterEventItem[]>(response, "Failed to fetch dead letter events");
}

export async function fetchDeadLetterEventDetail(id: string): Promise<DeadLetterEventDetail> {
  const response = await fetch(`${API_BASE_URL}/api/dead-letter-events/${id}`);
  return parseResponse<DeadLetterEventDetail>(response, "Failed to fetch dead letter event detail");
}

export async function reprocessDeadLetterEvent(id: string): Promise<DeadLetterReprocessResponse> {
  const response = await fetch(`${API_BASE_URL}/api/dead-letter-events/${id}/reprocess`, {
    method: "POST"
  });
  return parseResponse<DeadLetterReprocessResponse>(response, "Failed to reprocess dead letter event");
}
