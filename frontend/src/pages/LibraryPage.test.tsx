import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../components/Toast";
import { LibraryPage } from "./LibraryPage";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <LibraryPage />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LibraryPage batch queueing (IMPROVEMENT_PLAN.md 4.1)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let jobRequests: string[];

  beforeEach(() => {
    jobRequests = [];
    fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/browse")) {
        return jsonResponse({
          path: "",
          entries: [
            { name: "S01E01.mkv", path: "S01E01.mkv", type: "video", size: 100, has_english_subtitle: false },
            { name: "S01E02.mkv", path: "S01E02.mkv", type: "video", size: 100, has_english_subtitle: true },
            { name: "S01E03.mkv", path: "S01E03.mkv", type: "video", size: 100, has_english_subtitle: false },
          ],
        });
      }
      if (url.startsWith("/api/jobs") && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        jobRequests.push(body.video_path);
        return jsonResponse({ job: { id: `job-${jobRequests.length}`, job_type: "video" } }, 201);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("batch mode is off by default -- no checkboxes rendered", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("entering batch mode shows the untranscribed count", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText("Batch queue…"));
    expect(await screen.findByText(/3 videos here, 2 untranscribed/)).toBeInTheDocument();
  });

  it("already-transcribed episodes are marked with a checkmark", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E02\.mkv/));
    expect(screen.getByTitle("English subtitle already exists")).toBeInTheDocument();
  });

  it("select all untranscribed only checks the two without an English subtitle", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText("Batch queue…"));
    fireEvent.click(await screen.findByText("Select all untranscribed"));
    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    const checked = checkboxes.filter((c) => c.checked);
    expect(checked).toHaveLength(2);
  });

  it("queuing the selection posts one job per selected video", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText("Batch queue…"));
    fireEvent.click(await screen.findByText("Select all untranscribed"));
    fireEvent.click(screen.getByText("Queue 2 selected"));

    await waitFor(() => expect(screen.getByText("Queued 2 jobs.")).toBeInTheDocument());
    expect(jobRequests.sort()).toEqual(["S01E01.mkv", "S01E03.mkv"]);
  });

  it("clear selection empties the checked set", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText("Batch queue…"));
    fireEvent.click(await screen.findByText("Select all untranscribed"));
    fireEvent.click(screen.getByText("Clear selection"));
    const checkboxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(checkboxes.every((c) => !c.checked)).toBe(true);
  });

  it("the queue button is disabled with nothing selected", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText("Batch queue…"));
    expect(await screen.findByText("Queue 0 selected")).toBeDisabled();
  });
});
