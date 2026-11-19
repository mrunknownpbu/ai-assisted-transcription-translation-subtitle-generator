import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MediaBrowser } from "./MediaBrowser";

const ENTRIES = [
  { name: "Love Is In The Air (2020)", path: "Love", type: "directory", size: 0 },
  { name: "Game of Thrones (2011)", path: "GoT", type: "directory", size: 0 },
  { name: "Movie.2019.mkv", path: "Movie.2019.mkv", type: "video", size: 1, has_english_subtitle: false },
];

function renderBrowser() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MediaBrowser selectedPath={null} onSelect={() => {}} />
    </QueryClientProvider>,
  );
}

describe("MediaBrowser filter", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ path: "", entries: ENTRIES }), { status: 200 })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("narrows rows by case-insensitive words in any order, and shows the count", async () => {
    renderBrowser();
    await screen.findByText(/Love Is In The Air/);
    const box = screen.getByLabelText("Filter files and folders");
    fireEvent.change(box, { target: { value: "AIR love" } });
    expect(screen.getByText(/Love Is In The Air/)).toBeInTheDocument();
    expect(screen.queryByText(/Game of Thrones/)).not.toBeInTheDocument();
    expect(screen.getByText("1 of 3")).toBeInTheDocument();
  });

  it("says when nothing matches and restores the list on Escape", async () => {
    renderBrowser();
    await screen.findByText(/Love Is In The Air/);
    const box = screen.getByLabelText("Filter files and folders");
    fireEvent.change(box, { target: { value: "zzz" } });
    expect(screen.getByText(/No match for/)).toBeInTheDocument();
    fireEvent.keyDown(box, { key: "Escape" });
    expect(screen.getByText(/Game of Thrones/)).toBeInTheDocument();
    expect(screen.queryByText(/No match for/)).not.toBeInTheDocument();
  });
});
