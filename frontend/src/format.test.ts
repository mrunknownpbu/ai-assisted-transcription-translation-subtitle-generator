import { describe, expect, it } from "vitest";

import { fmtDateTime } from "./format";

describe("fmtDateTime", () => {
  it("returns an em dash for null/undefined/zero", () => {
    expect(fmtDateTime(null)).toBe("—");
    expect(fmtDateTime(undefined)).toBe("—");
    expect(fmtDateTime(0)).toBe("—");
  });

  it("formats a Unix-seconds timestamp as a date and a time", () => {
    // 2026-01-15T10:30:00Z -- assert on parts rather than the whole
    // locale-formatted string, which varies by environment.
    const result = fmtDateTime(1768473000);
    expect(result).toMatch(/2026/);
    expect(result).toMatch(/:\d{2}/); // has a time component (HH:MM)
    expect(result).not.toBe("—");
  });
});
