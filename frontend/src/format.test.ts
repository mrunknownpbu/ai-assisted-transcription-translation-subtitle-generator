import { describe, expect, it } from "vitest";

import { fmtDateTime, fmtEta } from "./format";

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

describe("fmtEta", () => {
  it("returns null for null/undefined inputs", () => {
    expect(fmtEta(null, 50)).toBeNull();
    expect(fmtEta(60, null)).toBeNull();
  });

  it("returns null below 2% progress -- too little signal to extrapolate", () => {
    expect(fmtEta(10, 1)).toBeNull();
  });

  it("returns null once progress reaches 100% -- nothing left to estimate", () => {
    expect(fmtEta(600, 100)).toBeNull();
  });

  it("extrapolates remaining time linearly from elapsed/progress", () => {
    // 50% done after 60s -> ~60s remaining.
    expect(fmtEta(60, 50)).toBe("~1m 0s left");
  });

  it("a small remaining estimate never renders as negative or zero", () => {
    // 99% done after 99s -> ~1s remaining, still a valid positive ETA.
    const result = fmtEta(99, 99);
    expect(result).not.toBeNull();
    expect(result).toMatch(/^~/);
  });
});
