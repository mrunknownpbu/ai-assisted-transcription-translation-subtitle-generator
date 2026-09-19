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
  });

  it("shows the overwrite control only when an English subtitle already exists", async () => {
    existingSubtitles = ["S01E01.en.srt"];
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    expect(await screen.findByText("English subtitle already exists")).toBeInTheDocument();
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
