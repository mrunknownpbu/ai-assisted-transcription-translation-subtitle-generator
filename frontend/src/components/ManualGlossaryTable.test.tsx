import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "./Toast";
import { ManualGlossaryTable } from "./ManualGlossaryTable";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderTable() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ManualGlossaryTable
          tvdbId={383383}
          entries={[{ canonical: "Eda", surface_forms: ["Eda", "Eda Yıldız"] }]}
        />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("ManualGlossaryTable", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(() => jsonResponse({ manual_glossary: [] }, 200));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders entries with their aliases", () => {
    renderTable();
    expect(screen.getByText("Eda")).toBeInTheDocument();
    expect(screen.getByText("Eda Yıldız")).toBeInTheDocument();
  });

  it("clicking Edit shows inputs pre-filled with current values", () => {
    renderTable();
    fireEvent.click(screen.getByText("Edit"));
    expect(screen.getByLabelText("Canonical name")).toHaveValue("Eda");
    expect(screen.getByLabelText("Aliases")).toHaveValue("Eda Yıldız");
  });

  it("Save posts the expected body", async () => {
    renderTable();
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("Canonical name"), { target: { value: "Eda Yıldız" } });
    fireEvent.change(screen.getByLabelText("Aliases"), { target: { value: "Eda, Eda Hanım" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/series/383383/glossary/update",
      expect.objectContaining({ method: "POST" }),
    ));
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({
      original_canonical: "Eda",
      canonical: "Eda Yıldız",
      aliases: ["Eda", "Eda Hanım"],
    });
  });

  it("Cancel on edit reverts without calling the API", () => {
    renderTable();
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("Canonical name"), { target: { value: "Changed" } });
    fireEvent.click(screen.getByText("Cancel"));
    expect(screen.getByText("Eda")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("Delete then Confirm posts the expected body and removes the row", async () => {
    renderTable();
    fireEvent.click(screen.getByText("Delete"));
    fireEvent.click(screen.getByText("Confirm"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/series/383383/glossary/delete",
      expect.objectContaining({ method: "POST" }),
    ));
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ canonical: "Eda" });

    await waitFor(() =>
      expect(screen.getByText("No hand-curated glossary entries for this series.")).toBeInTheDocument(),
    );
  });

  it("Delete then Cancel reverts without calling the API", () => {
    renderTable();
    fireEvent.click(screen.getByText("Delete"));
    fireEvent.click(screen.getByText("Cancel"));
    expect(screen.getByText("Eda")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
