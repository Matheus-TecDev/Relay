import type { EventItem } from "../types/event";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export async function fetchEvents(): Promise<EventItem[]> {
  const response = await fetch(`${API_BASE_URL}/api/events`);

  if (!response.ok) {
    throw new Error("Failed to fetch events");
  }

  return response.json();
}

