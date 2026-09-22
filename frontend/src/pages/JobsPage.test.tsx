import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../components/Toast";
import { JobsPage } from "./JobsPage";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <JobsPage />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const RUNNING_JOB = {
  id: "job-1", job_type: "video", video_path: "Show/S01E01.mkv", source_srt_path: null,
  destination_srt_path: null, source_is_uploaded: false, status: "running", stage: "RUNNING",
  progress: 50, source_lang: "auto", target_lang: "en", detected_language: "tr",
  language_confidence: 0.9, source_language_mode: "AUTO", requested_audio_stream: null,
  selected_audio_stream: null, embedded_stream_language: null, stream_selection_mode: "AUTO",
  selected_stream_reason: null, overwrite_original: false, overwrite_english: false,
  created_at: 1768473000, started_at: 1768473005, finished_at: null, updated_at: 1768473010,
  error: null, error_category: null, outputs: [], qc: {}, cancel_requested: false,
  retry_of_job_id: null, attempt: 1, log: [], tvdb_id: null, elapsed_seconds: 30, needs_review: 0,
};

describe("JobsPage", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let requestedUrls: string[];

  beforeEach(() => {
    requestedUrls = [];
    fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      requestedUrls.push(url);
      if (url.startsWith("/api/jobs")) {
        const params = new URL(url, "http://x").searchParams;
        const status = params.get("status");
        // Mirrors the real backend's (now case-insensitive) filtering --
        // this fixture only has one lowercase-"running" job.
        const matches = !status || status.toLowerCase() === "running";
        return jsonResponse({ jobs: matches ? [RUNNING_JOB] : [], total: matches ? 1 : 0 });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("ALL tab sends no status filter and shows the job", async () => {
    renderPage();
    expect(await screen.findByText("S01E01.mkv")).toBeInTheDocument();
  });

  it("clicking the RUNNING tab sends a lowercase status param and still shows the job", async () => {
    // Real bug (2026-09-22): the tab label is uppercase ("RUNNING") but
    // the job store's status column is lowercase -- sending the label
    // unmodified made every non-ALL tab show "No jobs." regardless of
    // how many jobs actually had that status.
    renderPage();
    await screen.findByText("S01E01.mkv");
    fireEvent.click(screen.getByRole("tab", { name: "RUNNING" }));

    await waitFor(() => expect(requestedUrls.some((u) => u.includes("status=running"))).toBe(true));
    expect(await screen.findByText("S01E01.mkv")).toBeInTheDocument();
    expect(screen.queryByText("No jobs.")).not.toBeInTheDocument();
  });

  it("clicking the FAILED tab (no matching jobs) shows the empty state, not an error", async () => {
    renderPage();
    await screen.findByText("S01E01.mkv");
    fireEvent.click(screen.getByRole("tab", { name: "FAILED" }));
    expect(await screen.findByText("No jobs.")).toBeInTheDocument();
  });

  it("renders a formatted Date/Time column", async () => {
    renderPage();
    await screen.findByText("S01E01.mkv");
    expect(screen.getByText("Date/Time")).toBeInTheDocument();
    // The fixture's created_at (2026-01-15T10:30:00Z) renders as some
    // non-placeholder, year-containing text -- exact formatting is
    // locale-dependent (see format.test.ts for the unit-level assertion).
    const cells = screen.getAllByTitle(/2026/);
    expect(cells.length).toBeGreaterThan(0);
  });
});
