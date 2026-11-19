import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { fetchWithApiKey } from "./client";
import type { JobChangedEvent } from "./types";

/** Subscribes once (mounted at the app root) to GET /api/events and
 * invalidates the jobs/series/queue queries on every "job_changed"
 * signal -- replaces the old app's blind setInterval(loadJobs, 2000)
 * full-table poll with push-triggered refetch. The event body itself is
 * never trusted as the data (see events.py's docstring) -- only used to
 * know that something changed.
 *
 * EventSource cannot send X-API-Key, so this uses fetch() and parses the
 * small server-sent-event framing directly. The same API-key retry used by
 * every other browser request applies before the stream starts. */
export function useEventStream() {
  const queryClient = useQueryClient();

  useEffect(() => {
    let stopped = false;
    let controller: AbortController | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const handleEvent = (frame: string) => {
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice("data:".length).trimStart())
        .join("\n");
      if (!data) return;
      let event: JobChangedEvent;
      try {
        event = JSON.parse(data);
      } catch {
        return;
      }
      if (event.type !== "job_changed") return;
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["job", event.job_id] });
      queryClient.invalidateQueries({ queryKey: ["queue"] });
      queryClient.invalidateQueries({ queryKey: ["series"] });
    };

    // Retry delay: 2 s, doubling up to 30 s while the server keeps failing, reset once a
    // stream is established. A refused key (401/403) is not retried: it would only
    // re-prompt for the key every few seconds.
    let retryDelay = 2_000;

    const connect = async () => {
      controller = new AbortController();
      try {
        const response = await fetchWithApiKey("/api/events", {
          headers: { Accept: "text/event-stream" },
          signal: controller.signal,
        });
        if (response.status === 401 || response.status === 403) return;
        if (!response.ok || !response.body) {
          // e.g. 502/503 from the proxy or API while the container restarts
          if (!stopped) {
            reconnectTimer = setTimeout(connect, retryDelay);
            retryDelay = Math.min(retryDelay * 2, 30_000);
          }
          return;
        }
        retryDelay = 2_000;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffered = "";
        while (!stopped) {
          const { done, value } = await reader.read();
          if (done) break;
          buffered += decoder.decode(value, { stream: true });
          const frames = buffered.split(/\r?\n\r?\n/);
          buffered = frames.pop() ?? "";
          frames.forEach(handleEvent);
        }
      } catch (error) {
        if (stopped || (error instanceof DOMException && error.name === "AbortError")) return;
      }
      if (!stopped) reconnectTimer = setTimeout(connect, 2_000);
    };

    void connect();
    return () => {
      stopped = true;
      controller?.abort();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, [queryClient]);
}
