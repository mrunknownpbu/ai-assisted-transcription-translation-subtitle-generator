import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../components/Toast";
import { RetimePage, languageFromFilename, retimeDestination } from "./RetimePage";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <RetimePage />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function findRequest(fetchMock: ReturnType<typeof vi.fn>, url: string): RequestInit | undefined {
  const call = fetchMock.mock.calls.find((c) => c[0] === url);
  return call ? (call[1] as RequestInit) : undefined;
}

describe("helpers", () => {
  it("reads the language tag from a subtitle file name", () => {
    expect(languageFromFilename("Film.tr.srt")).toBe("tr");
    expect(languageFromFilename("Film.zh.retimed.srt")).toBe("zh");
    expect(languageFromFilename("Film.srt")).toBe("");
    expect(languageFromFilename("Film.English.srt")).toBe("");
  });

  it("previews the sidecar by default and the library file when replacing", () => {
    expect(retimeDestination("Show/S01E01.mkv", "tr", false)).toBe("Show/S01E01.tr.retimed.srt");
    expect(retimeDestination("Show/S01E01.mkv", "tr", true)).toBe("Show/S01E01.tr.srt");
  });
});

describe("RetimePage", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/browse")) {
        if (url.includes("file_type=srt")) {
          return jsonResponse({ path: "", entries: [{ name: "ep.tr.srt", path: "ep.tr.srt", type: "srt", size: 100 }] });
        }
        return jsonResponse({ path: "", entries: [{ name: "S01E01.mkv", path: "S01E01.mkv", type: "video", size: 100 }] });
      }
      if (url.startsWith("/api/subtitle-retimes")) {
        return jsonResponse({ job: { id: "job-1", job_type: "subtitle_retime" } }, 201);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function chooseVideoAndSubtitle() {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    await waitFor(() => screen.getByText(/ep\.tr\.srt/));
    fireEvent.click(screen.getByText(/ep\.tr\.srt/));
  }

  it("asks for the video first", () => {
    renderPage();
    expect(screen.getByText("Re-time existing subtitle")).toBeInTheDocument();
    expect(screen.getByText(/The text is never changed/)).toBeInTheDocument();
  });

  it("fills the language from the subtitle's name and previews the sidecar", async () => {
    await chooseVideoAndSubtitle();
    expect(await screen.findByDisplayValue("tr")).toBeInTheDocument();
    expect(screen.getByText(/Will write to: S01E01\.tr\.retimed\.srt/)).toBeInTheDocument();
  });

  it("submits the video, the subtitle and the language without replacing by default", async () => {
    await chooseVideoAndSubtitle();
    fireEvent.click(await screen.findByText("Re-time"));
    await waitFor(() => expect(findRequest(fetchMock, "/api/subtitle-retimes")).toBeTruthy());
    const body = JSON.parse(findRequest(fetchMock, "/api/subtitle-retimes")!.body as string);
    expect(body).toEqual({
      video_path: "S01E01.mkv",
      source_srt_path: "ep.tr.srt",
      language: "tr",
      replace_original: false,
    });
  });

  it("warns and sends replace_original when replacing the library subtitle", async () => {
    await chooseVideoAndSubtitle();
    fireEvent.click(await screen.findByLabelText(/Replace the library subtitle/));
    expect(screen.getByText(/Will write to: S01E01\.tr\.srt/)).toBeInTheDocument();
    expect(screen.getByText(/is overwritten/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Re-time"));
    await waitFor(() => expect(findRequest(fetchMock, "/api/subtitle-retimes")).toBeTruthy());
    expect(JSON.parse(findRequest(fetchMock, "/api/subtitle-retimes")!.body as string).replace_original).toBe(true);
  });

  it("translating afterwards sends both flags and previews the English file", async () => {
    await chooseVideoAndSubtitle();
    fireEvent.click(await screen.findByLabelText("Translate to English afterwards"));
    expect(screen.getByText(/translates the re-timed subtitle to S01E01\.en\.srt/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Replace an existing English subtitle"));
    fireEvent.click(screen.getByText("Re-time"));
    await waitFor(() => expect(findRequest(fetchMock, "/api/subtitle-retimes")).toBeTruthy());
    const body = JSON.parse(findRequest(fetchMock, "/api/subtitle-retimes")!.body as string);
    expect(body.translate_to_english).toBe(true);
    expect(body.overwrite_english).toBe(true);
  });

  it("sends no translation fields unless asked", async () => {
    await chooseVideoAndSubtitle();
    fireEvent.click(await screen.findByText("Re-time"));
    await waitFor(() => expect(findRequest(fetchMock, "/api/subtitle-retimes")).toBeTruthy());
    const body = JSON.parse(findRequest(fetchMock, "/api/subtitle-retimes")!.body as string);
    expect(body.translate_to_english).toBeUndefined();
    expect(body.overwrite_english).toBeUndefined();
  });

  it("cannot translate an English subtitle", async () => {
    await chooseVideoAndSubtitle();
    fireEvent.change(await screen.findByLabelText("Subtitle language"), { target: { value: "en" } });
    expect(screen.getByLabelText("Translate to English afterwards")).toBeDisabled();
  });

  it("cannot submit without a subtitle or with an invalid language", async () => {
    renderPage();
    await waitFor(() => screen.getByText(/S01E01\.mkv/));
    fireEvent.click(screen.getByText(/S01E01\.mkv/));
    expect(await screen.findByText("Re-time")).toBeDisabled();
    await waitFor(() => screen.getByText(/ep\.tr\.srt/));
    fireEvent.click(screen.getByText(/ep\.tr\.srt/));
    fireEvent.change(await screen.findByLabelText("Subtitle language"), { target: { value: "t" } });
    expect(screen.getByText("Re-time")).toBeDisabled();
    expect(screen.getByText(/2-3 letter code/)).toBeInTheDocument();
  });
});
