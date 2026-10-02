/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

import globalCss from "./global.css?raw";
import themeCss from "./theme.css?raw";

// Every stylesheet and component source, as text (tests excluded). CSS is
// imported by name because the glob does not return stylesheets under vitest.
const sources: Record<string, string> = {
  "../styles/theme.css": themeCss,
  "../styles/global.css": globalCss,
  ...Object.fromEntries(
    Object.entries(
      import.meta.glob<string>("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }),
    ).filter(([path]) => !/\.test\.tsx?$/.test(path)),
  ),
};

const COLOUR = /(?<![&\w])#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/;

describe("design tokens", () => {
  it("sees the sources it is meant to check", () => {
    expect(Object.keys(sources)).toEqual(
      expect.arrayContaining(["../styles/theme.css", "../styles/global.css", "../components/ProgressBar.tsx"]),
    );
    expect(sources["../styles/theme.css"]).toContain("--color-success:");
  });

  it("defines raw colours only in theme.css", () => {
    const offenders = Object.entries(sources)
      .filter(([path, text]) => !path.endsWith("theme.css") && COLOUR.test(text))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });

  it("keeps inline styles to the one data-driven case (progress width)", () => {
    const offenders = Object.entries(sources)
      .filter(([path, text]) => path.endsWith(".tsx") && /style=\{\{/.test(text))
      .map(([path]) => path);
    expect(offenders).toEqual(["../components/ProgressBar.tsx"]);
  });

  it("gives every semantic status colour a token", () => {
    const tokens = sources["../styles/theme.css"];
    for (const name of ["success", "warning", "error", "info", "neutral", "running", "queued", "cancelled"]) {
      expect(tokens).toContain(`--color-${name}:`);
    }
  });
});
