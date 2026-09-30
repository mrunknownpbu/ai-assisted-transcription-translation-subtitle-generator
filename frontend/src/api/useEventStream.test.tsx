import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_KEY_STORAGE } from "./client";
import { useEventStream } from "./useEventStream";

function Subscriber() {
  useEventStream();
  return null;
}

describe("useEventStream", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the stored API key while consuming and invalidating an SSE event", async () => {
    localStorage.setItem(API_KEY_STORAGE, "stream-key");
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(
            'data: {"type":"job_changed","job_id":"job-1"}\n\n',
          ));
          controller.close();
        },
      }),
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <Subscriber />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["job", "job-1"] }));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/events");
    expect((fetchMock.mock.calls[0][1] as RequestInit).headers).toEqual({
      Accept: "text/event-stream",
      "X-API-Key": "stream-key",
    });
    unmount();
  });
});
