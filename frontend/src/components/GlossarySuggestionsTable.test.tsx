import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "./Toast";
import { GlossarySuggestionsTable } from "./GlossarySuggestionsTable";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderTable() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <GlossarySuggestionsTable
          tvdbId={383383}
          suggestions={[
            { canonical: "Melek", aliases: [], protected: false, occurrences: 12, distinct_episodes: 1 },
          ]}
        />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("GlossarySuggestionsTable", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(() => jsonResponse({ manual_glossary: [{ canonical: "Melek", surface_forms: ["Melek"] }] }, 200));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a suggestion row with a Promote action", () => {
    renderTable();
    expect(screen.getByText("Melek")).toBeInTheDocument();
    expect(screen.getByText("Promote")).toBeInTheDocument();
  });

  it("clicking Promote posts the expected body and hides the row", async () => {
    renderTable();
    fireEvent.click(screen.getByText("Promote"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/series/383383/glossary/promote",
      expect.objectContaining({ method: "POST" }),
    ));
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ canonical: "Melek", aliases: [] });

    await waitFor(() =>
      expect(screen.getByText("No auto-mined suggestions yet for this series.")).toBeInTheDocument(),
    );
  });
});
