import { describe, expect, it } from "vitest";

import { episodeKey, sameEpisode } from "./episodeMatch";

describe("episodeKey", () => {
  it.each([
    ["Love Is In The Air (2020) S01E01.mkv", { season: 1, episode: 1 }],
    ["show.s02e15.720p.srt", { season: 2, episode: 15 }],
    ["Sen Çal Kapimi 1. Bölüm.srt", { season: null, episode: 1 }],
    ["Sen Çal Kapimi 12.Bölüm.tr.srt", { season: null, episode: 12 }],
    ["Show E07.srt", { season: null, episode: 7 }],
    ["Show Episode 3.srt", { season: null, episode: 3 }],
    ["Show - 04.srt", { season: null, episode: 4 }],
  ])("%s", (name, expected) => {
    expect(episodeKey(name)).toEqual(expected);
  });

  it("does not mistake a year or a plain title for an episode", () => {
    expect(episodeKey("Some Show (2020).srt")).toBeNull();
    expect(episodeKey("Some Show.srt")).toBeNull();
    expect(episodeKey("Pepper.srt")).toBeNull(); // 'Ep' inside a word
  });
});

describe("sameEpisode", () => {
  it("ignores season when either side has none", () => {
    expect(sameEpisode({ season: null, episode: 1 }, { season: 1, episode: 1 })).toBe(true);
  });
  it("compares season when both have one", () => {
    expect(sameEpisode({ season: 2, episode: 1 }, { season: 1, episode: 1 })).toBe(false);
  });
  it("is false for different episodes or unknown keys", () => {
    expect(sameEpisode({ season: null, episode: 2 }, { season: 1, episode: 1 })).toBe(false);
    expect(sameEpisode(null, { season: 1, episode: 1 })).toBe(false);
  });
});
