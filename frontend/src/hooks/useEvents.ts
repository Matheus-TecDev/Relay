import { useCallback, useEffect, useState } from "react";

import { fetchEvents } from "../api/events";
import type { EventItem } from "../types/event";

export function useEvents() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setIsLoading(true);
    return fetchEvents(100)
      .then((data) => {
        setEvents(data);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Unexpected error");
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { events, isLoading, error, refresh };
}
