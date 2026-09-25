import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../components/Toast";
import { TranslateSrtPage } from "./TranslateSrtPage";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <TranslateSrtPage />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function findRequest(fetchMock: ReturnType<typeof vi.fn>, url: string): RequestInit | undefined {
  const call = fetchMock.mock.calls.find((c) => c[0] === url);
  return call ? (call[1] as RequestInit) : undefined;
}

describe("TranslateSrtPage", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let existingSubtitles: string[];

  beforeEach(() => {
    existingSubtitles = [];
    fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/browse")) {
        if (url.includes("file_type=srt")) {
          return jsonResponse({
            path: "",
            entries: [{ name: "ep.tr.srt", path: "ep.tr.srt", type: "srt", size: 100 }],
          });
        }
        return jsonResponse({
          path: "",
          entries: [{ name: "S01E01.mkv", path: "S01E01.mkv", type: "video", size: 100 }],
        });
      }
      if (url.startsWith("/api/media")) {
        return jsonResponse({
          duration: 100, audio_tracks: [], path: "S01E01.mkv", filename: "S01E01.mkv",
          size: 100, existing_subtitles: existingSubtitles,
        });
      }
      if (url.startsWith("/api/languages")) {
        return jsonResponse({ languages: ["en", "tr"] });
      }
      if (url.startsWith("/api/srt-uploads")) {
        return jsonResponse({ upload_id: "up-1", filename: "fansub.srt" }, 201);
      }
      if (url.startsWith("/api/srt-translations")) {
        return jsonResponse({ job: { id: "job-1", job_type: "srt_translation" } }, 201);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the episode-selection prompt before a video is chosen", () => {
    renderPage();
    expect(screen.getByText("Translate existing subtitle")).toBeInTheDocument();
    expect(screen.getByText(/Select the episode this subtitle belongs to/)).toBeInTheDocument();
  });

  it("selecting a video reveals the destination preview and source tabs", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));

    expect(await screen.findByText(/Will write to: S01E01\.en\.srt/)).toBeInTheDocument();
    expect(screen.getByText("Browse library")).toBeInTheDocument();
    expect(screen.getByText("Upload from this computer")).toBeInTheDocument();
    expect(screen.queryByText("English subtitle already exists")).not.toBeInTheDocument();
    expect(screen.queryByText("Original-language subtitle already exists")).not.toBeInTheDocument();
  });

  it("shows the overwrite control only when an English subtitle already exists", async () => {
    existingSubtitles = ["S01E01.en.srt"];
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    expect(await screen.findByText("English subtitle already exists")).toBeInTheDocument();
  });

  it("shows the original-language overwrite control when a non-English subtitle already exists", async () => {
    existingSubtitles = ["S01E01.tr.srt"];
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    expect(await screen.findByText("Original-language subtitle already exists")).toBeInTheDocument();
  });

  it("does not treat a protected English variant as an original-language subtitle", async () => {
    existingSubtitles = ["S01E01.en.hi.srt"];
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    await screen.findByText("Browse library");
    expect(screen.queryByText("Original-language subtitle already exists")).not.toBeInTheDocument();
  });

  it("submits video_path + source_srt_path when a library file is selected", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    await waitFor(() => screen.getByText(/ep\.tr\.srt/));
    fireEvent.click(screen.getByText(/ep\.tr\.srt/));

    const submitButton = await screen.findByText("Translate");
    expect(submitButton).not.toBeDisabled();
    fireEvent.click(submitButton);

    await waitFor(() => expect(findRequest(fetchMock, "/api/srt-translations")).toBeTruthy());
    const body = JSON.parse(findRequest(fetchMock, "/api/srt-translations")!.body as string);
    expect(body.video_path).toBe("S01E01.mkv");
    expect(body.source_srt_path).toBe("ep.tr.srt");
    expect(body.source_upload_id).toBeUndefined();
    expect(body.overwrite_original).toBe(false);
  });

  it("toggling the original-language overwrite control to Replace sends overwrite_original: true", async () => {
    existingSubtitles = ["S01E01.tr.srt"];
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    await waitFor(() => screen.getByText(/ep\.tr\.srt/));
    fireEvent.click(screen.getByText(/ep\.tr\.srt/));

    const select = await screen.findByLabelText("Original-language subtitle already exists");
    fireEvent.change(select, { target: { value: "replace" } });

    fireEvent.click(await screen.findByText("Translate"));
    await waitFor(() => expect(findRequest(fetchMock, "/api/srt-translations")).toBeTruthy());
    const body = JSON.parse(findRequest(fetchMock, "/api/srt-translations")!.body as string);
    expect(body.overwrite_original).toBe(true);
  });

  it("uploading a file and submitting sends source_upload_id instead", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    fireEvent.click(await screen.findByText("Upload from this computer"));

    const file = new File(["1\n00:00:00,000 --> 00:00:01,000\nHi\n"], "fansub.srt", {
      type: "text/plain",
    });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(findRequest(fetchMock, "/api/srt-uploads")).toBeTruthy());
    expect(await screen.findByText("fansub.srt")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Translate"));
    await waitFor(() => expect(findRequest(fetchMock, "/api/srt-translations")).toBeTruthy());
    const body = JSON.parse(findRequest(fetchMock, "/api/srt-translations")!.body as string);
    expect(body.video_path).toBe("S01E01.mkv");
    expect(body.source_upload_id).toBe("up-1");
    expect(body.source_srt_path).toBeUndefined();
  });

  it("submit is disabled until a source is chosen", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    expect(await screen.findByText("Translate")).toBeDisabled();
  });
});

describe("TranslateSrtPage batch translate", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let posted: Array<Record<string, unknown>>;
  let failFor: string | null;

  const video = (n: number, extra: Record<string, unknown>) => ({
    name: `E0${n}.mkv`,
    path: `E0${n}.mkv`,
    type: "video",
    size: 100,
    has_english_subtitle: false,
    source_subtitles: [],
    ...extra,
  });

  beforeEach(() => {
    posted = [];
    failFor = null;
    fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/browse")) {
        return jsonResponse({
          path: "",
          entries: [
            video(1, { source_subtitles: ["E01.tr.srt"] }), // ready
            video(2, { has_english_subtitle: true, source_subtitles: ["E02.tr.srt"] }), // done
            video(3, {}), // no source subtitle
            video(4, { source_subtitles: ["E04.tr.srt", "E04.ar.srt"] }), // ambiguous
            video(5, { source_subtitles: ["E05.tr.srt"] }), // ready
          ],
        });
      }
      if (url.startsWith("/api/languages")) return jsonResponse({ languages: ["en", "tr"] });
      if (url === "/api/srt-translations" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        posted.push(body);
        if (failFor && body.video_path === failFor) return jsonResponse({ detail: "already queued" }, 409);
        return jsonResponse({ job: { id: `job-${posted.length}`, job_type: "srt_translation" } }, 201);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function openBatch() {
    renderPage();
    await waitFor(() => screen.getByText(/E01\.mkv/));
    fireEvent.click(screen.getByText("Batch translate…"));
    await screen.findByText(/ready to translate/);
  }

  it("batch mode is off by default -- no checkboxes", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/E01\.mkv/));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("shows how many episodes are ready and why the rest are skipped", async () => {
    await openBatch();
    expect(screen.getByText(/5 videos here:\s*2 ready to translate/)).toBeInTheDocument();
    expect(
      screen.getByText(/1 already have English, 1 have no source subtitle,\s*1 have several source subtitles/),
    ).toBeInTheDocument();
  });

  it("disables the checkbox for an episode that can't be queued, with the reason", async () => {
    await openBatch();
    const done = screen.getByLabelText("Select E02.mkv for batch queueing");
    expect(done).toBeDisabled();
    expect(done).toHaveAttribute("title", "English subtitle already exists");
    expect(screen.getByLabelText("Select E03.mkv for batch queueing")).toBeDisabled();
    // Several sources is ambiguous -- must not be auto-picked.
    expect(screen.getByLabelText("Select E04.mkv for batch queueing")).toBeDisabled();
    expect(screen.getByLabelText("Select E01.mkv for batch queueing")).not.toBeDisabled();
  });

  it("select all ready ticks only the two eligible episodes", async () => {
    await openBatch();
    fireEvent.click(screen.getByText("Select all ready"));
    const ticked = (screen.getAllByRole("checkbox") as HTMLInputElement[]).filter((c) => c.checked);
    expect(ticked).toHaveLength(2);
  });

  it("queue posts one job per ready episode with its OWN source subtitle", async () => {
    await openBatch();
    fireEvent.click(screen.getByText("Select all ready"));
    fireEvent.click(screen.getByText("Translate 2 selected"));

    await waitFor(() => expect(screen.getByText("Queued 2 translation jobs.")).toBeInTheDocument());
    const byVideo = Object.fromEntries(posted.map((b) => [b.video_path, b]));
    expect(byVideo["E01.mkv"].source_srt_path).toBe("E01.tr.srt");
    expect(byVideo["E05.mkv"].source_srt_path).toBe("E05.tr.srt");
    // Existing files are never replaced by a batch.
    expect(posted.every((b) => b.overwrite_english === false && b.overwrite_original === false)).toBe(true);
    expect(posted.every((b) => b.source_lang === "auto")).toBe(true);
    expect(posted).toHaveLength(2);
  });

  it("uses the source language chosen in the batch panel", async () => {
    await openBatch();
    fireEvent.change(screen.getByLabelText("Source language"), { target: { value: "tr" } });
    fireEvent.click(screen.getByText("Select all ready"));
    fireEvent.click(screen.getByText("Translate 2 selected"));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted.every((b) => b.source_lang === "tr")).toBe(true);
  });

  it("a failed episode stays ticked and the reason is shown", async () => {
    failFor = "E05.mkv";
    await openBatch();
    fireEvent.click(screen.getByText("Select all ready"));
    fireEvent.click(screen.getByText("Translate 2 selected"));

    await waitFor(() =>
      expect(screen.getByText("Queued 1, 1 failed to queue (already queued).")).toBeInTheDocument(),
    );
    const e1 = screen.getByLabelText("Select E01.mkv for batch queueing") as HTMLInputElement;
    const e5 = screen.getByLabelText("Select E05.mkv for batch queueing") as HTMLInputElement;
    expect(e1.checked).toBe(false); // queued -> cleared
    expect(e5.checked).toBe(true); // failed -> still ticked, retryable
  });

  it("replace-existing makes an episode that already has English eligible", async () => {
    await openBatch();
    fireEvent.click(screen.getByLabelText("Replace existing English subtitles"));
    // E02 has English + one source -> now ready; E03/E04 stay skipped for their own reasons.
    expect(screen.getByLabelText("Select E02.mkv for batch queueing")).not.toBeDisabled();
    expect(screen.getByLabelText("Select E03.mkv for batch queueing")).toBeDisabled();
    expect(screen.getByLabelText("Select E04.mkv for batch queueing")).toBeDisabled();
    expect(screen.getByText(/5 videos here:\s*3 ready to translate/)).toBeInTheDocument();
    expect(screen.getByText(/will be overwritten\s*\(1 in this folder have one\)/)).toBeInTheDocument();
  });

  it("replace-existing sends overwrite_english only for episodes that have English", async () => {
    await openBatch();
    fireEvent.click(screen.getByLabelText("Replace existing English subtitles"));
    fireEvent.click(screen.getByText("Select all ready"));
    fireEvent.click(screen.getByText("Translate 3 selected"));

    await waitFor(() => expect(posted).toHaveLength(3));
    const byVideo = Object.fromEntries(posted.map((b) => [b.video_path, b]));
    expect(byVideo["E02.mkv"].overwrite_english).toBe(true);
    expect(byVideo["E02.mkv"].source_srt_path).toBe("E02.tr.srt");
    expect(byVideo["E01.mkv"].overwrite_english).toBe(false);
    expect(byVideo["E05.mkv"].overwrite_english).toBe(false);
    expect(posted.every((b) => b.overwrite_original === false)).toBe(true);
  });

  it("turning replace-existing off again drops selections that are no longer eligible", async () => {
    await openBatch();
    const toggle = screen.getByLabelText("Replace existing English subtitles");
    fireEvent.click(toggle);
    fireEvent.click(screen.getByLabelText("Select E02.mkv for batch queueing"));
    fireEvent.click(toggle);
    expect(screen.getByText("Translate 0 selected")).toBeDisabled();
    expect(screen.getByLabelText("Select E02.mkv for batch queueing")).toBeDisabled();
  });

  it("the translate button is disabled with nothing selected", async () => {
    await openBatch();
    expect(screen.getByText("Translate 0 selected")).toBeDisabled();
  });
});
