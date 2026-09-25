import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SeriesListPage } from "./SeriesListPage";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SeriesListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const counts = (completed: number, failed = 0) => ({ QUEUED: 0, RUNNING: 0, COMPLETED: completed, FAILED: failed });

describe("SeriesListPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        if (String(input).startsWith("/api/series")) {
          return jsonResponse({
            series: [
              // 294 JOBS over 39 distinct episodes: re-runs and retries.
              { tvdb_id: 383383, title: "Love Is In The Air", total: 294, episodes: 39, counts: counts(294), last_updated: 2 },
              // The folder-name fallback title, one episode run three times.
              { tvdb_id: 449837, title: "Happy Kanako's Killer Life (2025)", total: 3, episodes: 1, counts: counts(1, 2), last_updated: 1 },
              // Every job is its own episode: no separate "jobs" count needed.
              { tvdb_id: 5, title: "Plain Show", total: 2, episodes: 2, counts: counts(2), last_updated: 0 },
            ],
          });
        }
        return jsonResponse({}, 404);
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("labels distinct episodes as episodes and the job count as jobs", async () => {
    renderPage();
    const card = (await screen.findByText("Love Is In The Air")).closest(".panel") as HTMLElement;
    expect(card).toHaveTextContent("39 episodes");
    expect(card).toHaveTextContent("294 jobs");
    expect(card).not.toHaveTextContent("294 episodes");
  });

  it("singular episode, plural jobs", async () => {
    renderPage();
    const card = (await screen.findByText("Happy Kanako's Killer Life (2025)")).closest(".panel") as HTMLElement;
    expect(card).toHaveTextContent("1 episode");
    expect(card).not.toHaveTextContent("1 episodes");
    expect(card).toHaveTextContent("3 jobs");
    expect(card).toHaveTextContent("2 failed");
  });

  it("omits the jobs count when it equals the episode count", async () => {
    renderPage();
    const card = (await screen.findByText("Plain Show")).closest(".panel") as HTMLElement;
    expect(card).toHaveTextContent("2 episodes");
    expect(card).not.toHaveTextContent("2 jobs");
  });

  it("still shows a series with no title as Series #id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        jsonResponse({
          series: [{ tvdb_id: 7, title: null, total: 1, episodes: 1, counts: counts(1), last_updated: 0 }],
        }),
      ),
    );
    renderPage();
    expect(await screen.findByText("Series #7")).toBeInTheDocument();
  });
});
