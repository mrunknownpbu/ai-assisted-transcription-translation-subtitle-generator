import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import type { JobChangedEvent } from "./types";

/** Subscribes once (mounted at the app root) to GET /api/events and
 * invalidates the jobs/series/queue queries on every "job_changed"
 * signal -- replaces the old app's blind setInterval(loadJobs, 2000)
 * full-table poll with push-triggered refetch. The event body itself is
 * never trusted as the data (see events.py's docstring) -- only used to
 * know that something changed. */
export function useEventStream() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const source = new EventSource("/api/events");

    source.onmessage = (message) => {
      let event: JobChangedEvent;
      try {
        event = JSON.parse(message.data);
      } catch {
        return;
      }
      if (event.type === "job_changed") {
        queryClient.invalidateQueries({ queryKey: ["jobs"] });
        queryClient.invalidateQueries({ queryKey: ["job", event.job_id] });
        queryClient.invalidateQueries({ queryKey: ["queue"] });
        queryClient.invalidateQueries({ queryKey: ["series"] });
      }
    };

    // EventSource auto-reconnects on transient failure by design (no
    // manual retry loop needed) -- onerror only needs to exist so a
    // dropped connection doesn't surface as an unhandled console error.
    source.onerror = () => {};

    return () => source.close();
  }, [queryClient]);
}
