import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CastReportPanel } from "./CastReportPanel";

describe("CastReportPanel", () => {
  it("explains an unchecked series", () => {
    render(<CastReportPanel report={null} />);
    expect(screen.getByText(/Not checked yet/)).toBeInTheDocument();
  });

  it("lists protected candidates first with their evidence", () => {
    render(
      <CastReportPanel
        report={{
          tvdb_id: 1,
          checked_at: 1790000000,
          added: ["Kiraz"],
          flags: ["Deniz: protected series-wide, but ..."],
          candidates: [
            { name: "Ayfer", full_names: [], nicknames: [], sources: ["tmdb"], credited_episodes: 52,
              scope: null, name_lines: 365, name_episodes: 40, probe: "1/30", examples: [],
              decision: "skip", reason: "translates correctly unprotected (1/30 lines lost it)" },
            { name: "Kiraz", full_names: ["Kiraz Yılmaz"], nicknames: [], sources: ["imdb", "tmdb"],
              credited_episodes: 13, scope: ["S02E01-E13"], name_lines: 35, name_episodes: 2,
              probe: "30/30", examples: [{ source: "Bu ne Kiraz?", unprotected: "What is this, Cherry?" }],
              decision: "protect", reason: "unprotected translation lost the name in 30/30 lines" },
          ],
        }}
      />,
    );
    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Kiraz");
    expect(rows[1]).toHaveTextContent("Protected");
    expect(rows[2]).toHaveTextContent("Not protected");
    expect(screen.getByText(/protected: Kiraz/)).toBeInTheDocument();
    expect(screen.getByText(/Note: Deniz/)).toBeInTheDocument();
    expect(screen.getByTitle("Bu ne Kiraz? → What is this, Cherry?")).toHaveTextContent("30/30");
  });

  it("shows a failed check", () => {
    render(<CastReportPanel report={{ tvdb_id: 1, checked_at: 1790000000, error: "boom" }} />);
    expect(screen.getByText(/failed: boom/)).toBeInTheDocument();
  });
});
