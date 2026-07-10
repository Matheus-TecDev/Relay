import { apiRequest } from "./client";
import type { DashboardSummary, EventDetail, EventItem } from "../types/event";

export function fetchEvents(limit = 100): Promise<EventItem[]> {
  return apiRequest<EventItem[]>(`/api/events?limit=${limit}`);
}

export function fetchEventDetail(id: string): Promise<EventDetail> {
  return apiRequest<EventDetail>(`/api/events/${id}`);
}

export function fetchEventsSummary(): Promise<DashboardSummary> {
  return apiRequest<DashboardSummary>("/api/events/summary");
}
