// Guess which episode a file name refers to, so files uploaded from a PC can be
// pre-assigned to the matching video. A best-effort HINT only: the user sees
// and can change every assignment before anything is queued.
export interface EpisodeKey {
  season: number | null;
  episode: number;
}

// Word for "episode" in the languages this project is used with. Turkish
// releases name files like "Sen Çal Kapimi 1. Bölüm.srt" (no S01E01 at all).
const EPISODE_WORDS = "Bölüm|Bolum|Episode|Episodio|Épisode|Episódio|Folge|Ep";

export function episodeKey(fileName: string): EpisodeKey | null {
  const stem = fileName.replace(/\.[A-Za-z0-9]{2,4}$/, "");

  const sxe = /S(\d{1,2})\s*E(\d{1,3})/i.exec(stem);
  if (sxe) return { season: Number(sxe[1]), episode: Number(sxe[2]) };

  // "1. Bölüm", "12 Episode": number BEFORE the word.
  const before = new RegExp(`(\\d{1,3})\\s*\\.?\\s*(?:${EPISODE_WORDS})(?![A-Za-z])`, "i").exec(stem);
  if (before) return { season: null, episode: Number(before[1]) };

  // "E01", "Ep 3", "Episode 12", "Bölüm 4": number AFTER the word.
  const after = new RegExp(`(?:^|[^A-Za-z])(?:${EPISODE_WORDS}|E)[\\s._-]*(\\d{1,3})(?!\\d)`, "i").exec(stem);
  if (after) return { season: null, episode: Number(after[1]) };

  // Last resort: "Show - 01". Needs a separator and to END the name, so a year
  // in the middle ("(2020)") is never mistaken for an episode.
  const trailing = /(?:^|[\s._-])(\d{1,3})$/.exec(stem);
  if (trailing) return { season: null, episode: Number(trailing[1]) };

  return null;
}

// Same episode? Season is only compared when BOTH sides state one (an upload
// named "1. Bölüm" is matched within whatever folder/season it is queued for).
export function sameEpisode(a: EpisodeKey | null, b: EpisodeKey | null): boolean {
  if (!a || !b) return false;
  if (a.episode !== b.episode) return false;
  return a.season == null || b.season == null || a.season === b.season;
}
