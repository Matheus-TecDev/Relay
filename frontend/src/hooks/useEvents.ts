import { useEffect, useState } from "react";

import { fetchEvents } from "../api/events";
import type { EventItem } from "../types/event";

export function useEvents() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;

    fetchEvents()
      .then((data) => {
        if (isMounted) {
          setEvents(data);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (isMounted) {
          setError(reason instanceof Error ? reason.message : "Unexpected error");
        }
      })
      .finally(() => {
        if (isMounted) {
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  return { events, isLoading, error };
}

