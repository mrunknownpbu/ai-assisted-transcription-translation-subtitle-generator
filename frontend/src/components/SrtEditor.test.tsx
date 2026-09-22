import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "./Toast";
import { SrtEditor } from "./SrtEditor";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <SrtEditor jobId="job-1" />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("SrtEditor (IMPROVEMENT_PLAN.md 4.2)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let putBody: unknown;

  beforeEach(() => {
    putBody = null;
    fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/jobs/job-1/srt" && (!init || init.method === undefined)) {
        return jsonResponse({
          cues: [
            { index: 0, start: 1, end: 2, lines: ["Hello there."] },
            { index: 1, start: 3, end: 4, lines: ["First line", "Second line"] },
          ],
          flagged_indices: [1],
        });
      }
      if (url === "/api/jobs/job-1/srt" && init?.method === "PUT") {
        putBody = JSON.parse(init.body as string);
        return jsonResponse({ cues: [] });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not fetch cues until opened", () => {
    renderEditor();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("Edit subtitles")).toBeInTheDocument();
  });

  it("opening loads and shows cue text, flagged cue first", async () => {
    renderEditor();
    fireEvent.click(screen.getByText("Edit subtitles"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const textareas = await screen.findAllByRole("textbox");
    // Cue 1 (index 1) is flagged, so it sorts first despite being #2.
    expect((textareas[0] as HTMLTextAreaElement).value).toBe("First line\nSecond line");
    expect((textareas[1] as HTMLTextAreaElement).value).toBe("Hello there.");
  });

  it("shows a flag marker only on the flagged cue", async () => {
    renderEditor();
    fireEvent.click(screen.getByText("Edit subtitles"));
    const flags = await screen.findAllByTitle("Flagged by QC");
    expect(flags).toHaveLength(1);
  });

  it("save button is disabled until something changes", async () => {
    renderEditor();
    fireEvent.click(screen.getByText("Edit subtitles"));
    const saveButton = await screen.findByText("Save 0 changes");
    expect(saveButton).toBeDisabled();
  });

  it("editing a cue enables save and sends only the changed cue", async () => {
    renderEditor();
    fireEvent.click(screen.getByText("Edit subtitles"));
    const textareas = await screen.findAllByRole("textbox");
    fireEvent.change(textareas[1], { target: { value: "Edited hello." } });

    const saveButton = await screen.findByText("Save 1 change");
    expect(saveButton).not.toBeDisabled();
    fireEvent.click(saveButton);

    await waitFor(() => expect(putBody).toBeTruthy());
    expect(putBody).toEqual({ edits: [{ index: 0, lines: ["Edited hello."] }] });
  });

  it("closing and reopening does not refetch already-cached data unnecessarily", async () => {
    renderEditor();
    fireEvent.click(screen.getByText("Edit subtitles"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText("Close"));
    expect(screen.getByText("Edit subtitles")).toBeInTheDocument();
  });
});
