import { useCallback, useEffect, useState } from "react";

import { fetchDeadLetterEvents } from "../api/deadLetterEvents";
import type { DeadLetterEventItem } from "../types/deadLetterEvent";

export function useDeadLetterEvents() {
  const [deadLetterEvents, setDeadLetterEvents] = useState<DeadLetterEventItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setIsLoading(true);
    return fetchDeadLetterEvents()
      .then((data) => {
        setDeadLetterEvents(data);
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
    let isMounted = true;

    fetchDeadLetterEvents()
      .then((data) => {
        if (isMounted) {
          setDeadLetterEvents(data);
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

  return { deadLetterEvents, isLoading, error, refresh };
}
