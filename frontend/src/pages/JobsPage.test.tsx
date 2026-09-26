import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useQueueCounts } from "../api/hooks";
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
  let extraJobs: Array<Record<string, unknown>>;

  beforeEach(() => {
    requestedUrls = [];
    extraJobs = [];
    fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      requestedUrls.push(url);
      if (url.startsWith("/api/jobs")) {
        const params = new URL(url, "http://x").searchParams;
        const status = params.get("status");
        // Mirrors the real backend's (now case-insensitive) filtering --
        // this fixture only has one lowercase-"running" job.
        const matches = !status || status.toLowerCase() === "running";
        if (extraJobs.length > 0) return jsonResponse({ jobs: extraJobs, total: extraJobs.length });
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
  it("an SRT job shows the episode file name, never the internal upload hash", async () => {
    extraJobs = [
      {
        ...RUNNING_JOB, id: "job-up", job_type: "srt_translation", status: "queued",
        video_path: "Show/Season 01/Show S01E05.mkv",
        source_srt_path: "0b70c0d7b1ad4250895c336647eeee85.srt", source_is_uploaded: true,
      },
    ];
    renderPage();
    const cell = await screen.findByText("Show S01E05.mkv");
    expect(screen.queryByText(/0b70c0d7/)).not.toBeInTheDocument();
    // The source stays discoverable from the tooltip.
    expect(cell).toHaveAttribute("title", expect.stringContaining("Source: uploaded from computer"));
  });

  it("a library-sourced SRT job's tooltip names its source subtitle", async () => {
    extraJobs = [
      {
        ...RUNNING_JOB, id: "job-lib", job_type: "srt_translation", status: "queued",
        video_path: "Show/S01E06.mkv", source_srt_path: "Show/S01E06.tr.srt", source_is_uploaded: false,
      },
    ];
    renderPage();
    const cell = await screen.findByText("S01E06.mkv");
    expect(cell).toHaveAttribute("title", expect.stringContaining("Source: Show/S01E06.tr.srt"));
  });
});

// The header's "N running" line (App's QueueSummary) is a SEPARATE query from
// the job list. Real bug (2026-09-26 screenshot): the Refresh button reloaded
// only the list, so after a job finished the header said "0 running" while the
// list still showed it running -- and the click gave no sign it did anything.
function HeaderProbe() {
  const { data } = useQueueCounts();
  return <span data-testid="header">{data ? `${data.RUNNING} running / ${data.COMPLETED} completed` : "..."}</span>;
}

describe("JobsPage Refresh button", () => {
  let state: { job: Record<string, unknown>; counts: Record<string, number> };
  let release: (() => void) | null;
  let holdJobs: boolean;
  let fetchCounts: Record<string, number>;

  beforeEach(() => {
    fetchCounts = { jobs: 0, queue: 0, series: 0 };
    holdJobs = false;
    release = null;
    state = {
      job: { ...RUNNING_JOB, id: "j", status: "running", stage: "Translating", progress: 24 },
      counts: { QUEUED: 0, RUNNING: 1, COMPLETED: 296, FAILED: 0, SKIPPED: 0, CANCELLED: 0, ALL: 297 },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/jobs")) {
          fetchCounts.jobs += 1;
          const snapshot = JSON.stringify({ jobs: [state.job], total: 1 });
          if (holdJobs) await new Promise<void>((r) => (release = r));
          return new Response(snapshot, { status: 200 });
        }
        if (url.startsWith("/api/queue")) {
          fetchCounts.queue += 1;
          return new Response(JSON.stringify(state.counts), { status: 200 });
        }
        if (url.startsWith("/api/series")) {
          fetchCounts.series += 1;
          return new Response(JSON.stringify({ series: [] }), { status: 200 });
        }
        return new Response("{}", { status: 404 });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function renderWithHeader() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ToastProvider>
            <HeaderProbe />
            <JobsPage />
          </ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  const finishTheJob = () => {
    state.job = { ...state.job, status: "completed", stage: "COMPLETED", progress: 100 };
    state.counts = { ...state.counts, RUNNING: 0, COMPLETED: 297 };
  };

  it("refreshes the header counts as well as the job list", async () => {
    renderWithHeader();
    expect(await screen.findByText("running")).toBeInTheDocument();
    expect(screen.getByTestId("header")).toHaveTextContent("1 running / 296 completed");

    finishTheJob();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    expect(await screen.findByText("completed")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("header")).toHaveTextContent("0 running / 297 completed"));
  });

  it("re-requests the list on every click, even when nothing changed", async () => {
    renderWithHeader();
    await screen.findByText("running");
    const before = fetchCounts.jobs;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(fetchCounts.jobs).toBe(before + 1));
  });

  it("shows it is working while the reload is in flight, then goes back to normal", async () => {
    renderWithHeader();
    await screen.findByText("running");
    holdJobs = true;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    const busy = await screen.findByRole("button", { name: "Refreshing…" });
    expect(busy).toBeDisabled();

    holdJobs = false;
    release?.();
    expect(await screen.findByRole("button", { name: "Refresh" })).toBeEnabled();
  });

  it("shows when the list was last updated", async () => {
    renderWithHeader();
    expect(await screen.findByText(/Updated \d{1,2}:\d{2}/)).toBeInTheDocument();
  });
});
