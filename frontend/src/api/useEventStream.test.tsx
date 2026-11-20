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
    vi.useRealTimers();
  });

  const sseResponse = () => new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(
          'data: {"type":"job_changed","job_id":"job-2"}\n\n',
        ));
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );

  it("reconnects after a non-OK response such as a 503 while the server restarts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("restarting", { status: 503 }))
      .mockImplementation(() => Promise.resolve(sseResponse()));
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <Subscriber />
      </QueryClientProvider>,
    );

    await vi.advanceTimersByTimeAsync(2_500);
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["job", "job-2"] }));
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    unmount();
  });

  it("does not hammer the server or re-prompt when the API key is refused", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const prompt = vi.fn().mockReturnValue(null);
    vi.stubGlobal("prompt", prompt);
    const fetchMock = vi.fn().mockImplementation(
      () => Promise.resolve(new Response("no", { status: 401 })));
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <Subscriber />
      </QueryClientProvider>,
    );

    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(prompt).toHaveBeenCalledTimes(1);
    unmount();
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
