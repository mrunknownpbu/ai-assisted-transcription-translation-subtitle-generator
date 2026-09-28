#!/usr/bin/env python3
"""Score transcription (ASR) against the library's human subtitles.

Many series keep a human original-language subtitle beside each episode
(`<stem>.<lang>.srt`; Love Is In The Air: all 39 S01 episodes, Turkish),
timed to the same release. Each system's transcript -- after the same
hallucination suppression production applies -- is compared with it in
60-second windows (by midpoint, so small timing differences don't count),
and reports:

* WER on dialogue, split into wrong / missed / extra words, plus CER;
* lyrics coverage: quoted song lyrics are scored separately, because
  Whisper mostly skips singing and they'd swamp the dialogue numbers;
* name recall: of the character names the human subtitle contains (the
  series glossary's protected names), how many the transcript also has --
  the errors that matter most downstream ("Serkan" -> "Sarkan");
* worst minutes and most common substitutions, to see *what* goes wrong;
* for each system after the first, the WER difference with a paired
  bootstrap 95% interval over windows.

Subtitles condense speech, so absolute WER is inflated (a subtitle drops
fillers and repetitions a correct transcript keeps). Use it to compare
settings, not as an accuracy claim. Two things are normalised on both
sides so they don't count as errors: numerals vs number words ("10" / "on")
and a short list of colloquial spellings ("valla" / "vallahi").

A reference this app wrote itself (a video job's committed `<lang>.srt`)
would score its own output against itself -- such references are skipped,
detected from the job database, or failing that from near-identity.

Systems:
  cache                 newest cached production transcript per episode
                        (/cache/transcripts; no GPU, instant)
  <system>+names        the same, then name_correction.py applied to it
  asr:key=val,...       run production ASR with AsrConfig overrides, e.g.
                        asr:model_name=large-v3,beam_size=5,vad_onset=0.3
                        (GPU, ~6 min/episode; saved under --work and reused)

Run inside the app container:
    docker cp scripts/eval_transcription.py subtitle-ai:/tmp/
    docker exec -e PYTHONPATH=/app subtitle-ai python /tmp/eval_transcription.py \\
        --season "/data/media/drama/turkish/Love Is In The Air (2020) {tvdb-383383}/Season 01" \\
        --episodes 1-4 --system cache --json /tmp/asr-eval.json
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

WINDOW = 60.0

# --- normalisation ------------------------------------------------------------

_UNITS = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
# Colloquial spoken form -> the written form subtitles use. Both sides are
# mapped, so either spelling scores as a match. Extend from real data only.
VARIANTS = {"valla": "vallahi", "abicim": "abiciğim", "baya": "bayağı", "napıyorsun": "ne yapıyorsun",
            "naber": "ne haber", "yo": "yok", "bi": "bir", "tabi": "tabii", "eee": "ee", "eeee": "ee"}


def turkish_number(n: int) -> list[str]:
    """0..9999 as Turkish number words: 25 -> ["yirmi", "beş"]."""
    if n == 0:
        return ["sıfır"]
    words = []
    thousands, rest = divmod(n, 1000)
    if thousands:
        words += ([] if thousands == 1 else [_UNITS[thousands]]) + ["bin"]
    hundreds, rest = divmod(rest, 100)
    if hundreds:
        words += ([] if hundreds == 1 else [_UNITS[hundreds]]) + ["yüz"]
    tens, units = divmod(rest, 10)
    words += [w for w in (_TENS[tens], _UNITS[units]) if w]
    return words


def normalise(text: str) -> list[str]:
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]|<[^>]+>|[♪♫]", " ", text)
    text = text.replace("I", "ı").replace("İ", "i").lower()
    text = text.replace("â", "a").replace("î", "i").replace("û", "u")
    text = re.sub(r"['’]", "", text)                  # Serkan'ın == Serkanın
    text = re.sub(r"[^\w\s]", " ", text)
    out: list[str] = []
    for token in text.split():
        m = re.fullmatch(r"(\d{1,4})(\D*)", token)    # "10" or "10da" (from 10'da)
        if m:
            words = turkish_number(int(m.group(1)))
            words[-1] += m.group(2)
            out += words
        else:
            out += VARIANTS.get(token, token).split()
    return out


# --- alignment ----------------------------------------------------------------

def align(ref: list[str], hyp: list[str]) -> tuple[int, int, int, list[tuple[str, str]]]:
    """(substitutions, deletions, insertions, [(ref_word, hyp_word), ...])."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        row, prev, r = d[i], d[i - 1], ref[i - 1]
        for j in range(1, m + 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (r != hyp[j - 1]))
    # Walk back through the table. Where several moves are equally cheap,
    # pair up words only if they look alike (so "serkan -> sarkan" is
    # reported, not "eve -> sarkan" plus a missing "serkan"); the WER is the
    # same either way, but the substitution list is what people read.
    i, j, s, dl, ins, subs = n, m, 0, 0, 0, []
    while i or j:
        diag = bool(i and j) and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1])
        up = bool(i) and d[i][j] == d[i - 1][j] + 1
        left = bool(j) and d[i][j] == d[i][j - 1] + 1
        if diag and (ref[i - 1] == hyp[j - 1] or _similar(ref[i - 1], hyp[j - 1]) or not (up or left)):
            if ref[i - 1] != hyp[j - 1]:
                s += 1
                subs.append((ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif up:
            dl, i = dl + 1, i - 1
        else:
            ins, j = ins + 1, j - 1
    return s, dl, ins, subs


def _similar(a: str, b: str) -> bool:
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.5


def chars(ref: list[str], hyp: list[str]) -> tuple[int, int]:
    a, b = " ".join(ref), " ".join(hyp)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1], len(a)


def windows(items, key_time, key_text) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for item in items:
        out.setdefault(int(key_time(item) // WINDOW), []).extend(normalise(key_text(item)))
    return out


# --- scoring ------------------------------------------------------------------

def name_counts(tokens: list[str], names: dict[str, str]) -> collections.Counter:
    """`names`: normalised name -> display name. Counts name tokens, allowing
    a suffix glued on (Serkanın, Edaya) since apostrophes are stripped."""
    c = collections.Counter()
    for t in tokens:
        for key, display in names.items():
            if t == key or (t.startswith(key) and len(t) - len(key) <= 4):
                c[display] += 1
                break
    return c


def score(ref_w: dict[int, list[str]], hyp_w: dict[int, list[str]], names: dict[str, str]) -> dict:
    per_window = {}
    tot = collections.Counter()
    subs: collections.Counter = collections.Counter()
    name_ref, name_hit = collections.Counter(), collections.Counter()
    for k in sorted(set(ref_w) | set(hyp_w)):
        r, h = ref_w.get(k, []), hyp_w.get(k, [])
        s, dl, ins, pairs = align(r, h)
        ce, cn = chars(r, h)
        per_window[k] = (s + dl + ins, len(r))
        tot.update(s=s, d=dl, i=ins, n=len(r), ce=ce, cn=cn)
        subs.update(pairs)
        rn, hn = name_counts(r, names), name_counts(h, names)
        for name, count in rn.items():
            name_ref[name] += count
            name_hit[name] += min(count, hn.get(name, 0))
    n = tot["n"] or 1
    return {"wer": 100 * (tot["s"] + tot["d"] + tot["i"]) / n, "subst": 100 * tot["s"] / n,
            "missed": 100 * tot["d"] / n, "extra": 100 * tot["i"] / n,
            "cer": 100 * tot["ce"] / (tot["cn"] or 1), "ref_words": tot["n"], "windows": per_window,
            "substitutions": subs, "name_ref": name_ref, "name_hit": name_hit}


def bootstrap(a: dict, b: dict, rounds: int = 1000, seed: int = 11) -> tuple[float, float]:
    """95% interval of WER(b) - WER(a) over shared (episode, window) keys."""
    keys = sorted(set(a) & set(b))
    rng = random.Random(seed)
    deltas = []
    for _ in range(rounds):
        sample = [keys[rng.randrange(len(keys))] for _ in keys]
        ea = sum(a[k][0] for k in sample); na = sum(a[k][1] for k in sample) or 1
        eb = sum(b[k][0] for k in sample); nb = sum(b[k][1] for k in sample) or 1
        deltas.append(100 * (eb / nb - ea / na))
    deltas.sort()
    return deltas[int(0.025 * rounds)], deltas[int(0.975 * rounds)]


# --- transcripts --------------------------------------------------------------

def production_params() -> dict:
    import asr
    c = asr.AsrConfig()
    return {"model": c.model_name, "compute_type": c.compute_type, "beam_size": c.beam_size,
            "hotwords": None}


def cached_transcript(video: Path, cache_dir: str = "/cache/transcripts"):
    """Newest cached transcript for `video` made with production settings."""
    want = production_params()
    best = None
    for f in glob.glob(f"{cache_dir}/*.json"):
        with open(f, encoding="utf-8") as fh:
            head = fh.read(4000)
        if json.dumps(str(video))[1:-1][:60] not in head and str(video)[:60] not in head:
            continue
        data = json.load(open(f, encoding="utf-8"))
        if data.get("media_path") != str(video):
            continue
        model = data.get("asr_model") or {}
        p = model.get("parameters") or {}
        if (model.get("version") != want["model"] or p.get("compute_type") != want["compute_type"]
                or p.get("beam_size") != want["beam_size"] or p.get("hotwords")):
            continue
        if best is None or data.get("created_at", 0) > best.get("created_at", 0):
            best = data
    return best


def run_asr(video: Path, overrides: dict, work: Path, stream: int | None):
    """Production transcription with AsrConfig overrides; saved and reused."""
    import asr
    import media
    from transcript import CanonicalTranscript
    work.mkdir(parents=True, exist_ok=True)
    out = work / (hashlib.sha1(str(video).encode()).hexdigest()[:16] + ".json")
    if out.is_file():
        return json.loads(out.read_text(encoding="utf-8"))
    config = asr.AsrConfig(**overrides)
    if stream is None:
        cached = cached_transcript(video)
        stream = cached["audio_stream_index"] if cached else None
    if stream is None:
        import audio_streams
        stream = audio_streams.recommend_stream(video, work).recommended_index
    wav = work / "audio.wav"
    media.extract_audio(video, stream, wav)
    from gpu import gpu_lock
    with gpu_lock():
        transcript: CanonicalTranscript = asr.transcribe(str(wav), str(video), "eval", stream, config=config)
    wav.unlink(missing_ok=True)
    transcript.save(out)
    return json.loads(out.read_text(encoding="utf-8"))


def kept_segments(data: dict) -> list:
    """Segments production keeps: hallucination.detect() marks the rest."""
    import hallucination
    from transcript import CanonicalTranscript
    t = CanonicalTranscript.from_dict(data)
    hallucination.detect(t.segments, t.language)
    return [s for s in t.segments if not getattr(s, "suppressed", False)]


def segment_text(seg) -> str:
    return re.sub(r"\s+(['’])", r"\1", " ".join(w.text for w in seg.words))


# --- references ---------------------------------------------------------------

def app_written(ref: Path, db: str = "/cache/jobs.db") -> bool:
    """True if the LAST job to commit this subtitle file was a video job,
    i.e. what is on disk now is this app's own transcript. Order matters:
    in this library early video jobs wrote S01E01-E05.tr.srt, and later
    subtitle-translation jobs (human uploads) wrote them again -- such a
    job re-commits its human source beside the video."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute("SELECT job_type, outputs, finished_at FROM jobs WHERE outputs LIKE ? "
                            "ORDER BY finished_at", (f"%{ref.name}%",)).fetchall()
    except sqlite3.Error:
        return False
    writers = [job_type for job_type, outputs, _ in rows if str(ref) in json.loads(outputs or "[]")]
    return bool(writers) and writers[-1] == "video"


def series_names(season: Path, glossary_dir: str) -> dict[str, str]:
    import glossary_profile
    tvdb = glossary_profile.find_tvdb_id(str(season))
    if tvdb is None or not os.path.isdir(glossary_dir):
        return {}
    profile = glossary_profile.load_profile(glossary_dir, tvdb_id=tvdb, all_episodes=True,
                                            enrich_from_tvdb=False)
    names = {}
    for e in profile.entities:
        for form in e.surface_forms:
            tokens = normalise(form)
            if len(tokens) == 1 and len(tokens[0]) >= 3:
                names[tokens[0]] = e.canonical
    return names


def is_lyric(text: str) -> bool:
    t = text.strip().lstrip("-– ").strip()
    return t[:1] in ('"', "“", "♪", "♫")


def parse_episodes(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", required=True)
    ap.add_argument("--episodes", default="1-4")
    ap.add_argument("--lang", default="tr")
    ap.add_argument("--system", action="append", required=True, help="cache | asr:key=val,...")
    ap.add_argument("--work", default="/cache/eval-asr")
    ap.add_argument("--glossary-dir", default="/glossary")
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    from srt import parse
    season = Path(args.season)
    names = series_names(season, args.glossary_dir)
    episodes = []
    for number in parse_episodes(args.episodes):
        video = next(iter(sorted(season.glob(f"*E{number:02d}.mkv")) + sorted(season.glob(f"*E{number:02d}.mp4"))), None)
        ref = next(iter(season.glob(f"*E{number:02d}.{args.lang}.srt")), None)
        if not video or not ref:
            print(f"E{number:02d}: skipped (no video or no .{args.lang}.srt)")
            continue
        if app_written(ref):
            print(f"E{number:02d}: skipped ({ref.name} was written by this app, not a person)")
            continue
        cues = parse(ref)
        # Song lyrics are quoted in these subtitles ("Gün olur, ben de gelirim").
        # Whisper mostly skips singing; scored with dialogue they made the
        # theme-song minutes 100% "missed" and hid the dialogue error rate,
        # so they're scored on their own (lyrics coverage).
        dialogue = [c for c in cues if not is_lyric(c.text)]
        lyric_times = [((c.start + c.end) / 2, c.text) for c in cues if is_lyric(c.text)]
        episodes.append((f"E{number:02d}", video, ref,
                         windows(dialogue, lambda c: (c.start + c.end) / 2, lambda c: c.text), lyric_times))

    results = []
    import cast_enrichment
    import glossary_profile
    import name_correction
    tvdb_id = glossary_profile.find_tvdb_id(str(season))
    cast_report = cast_enrichment.load_report(tvdb_id) if tvdb_id is not None else None

    for spec in args.system:
        post_names = spec.endswith("+names")
        base_spec = spec[:-len("+names")] if post_names else spec
        kind, _, rest = base_spec.partition(":")
        overrides = {}
        for kv in filter(None, rest.split(",")):
            k, _, v = kv.partition("=")
            overrides[k] = type(getattr(__import__("asr").AsrConfig(), k))(v) if v not in ("None",) else None
        per_episode, all_windows = {}, {}
        agg_subs, agg_ref, agg_hit = collections.Counter(), collections.Counter(), collections.Counter()
        tot = collections.Counter()
        t0 = time.time()
        lyric_words = lyric_hit = 0
        fixes = collections.Counter()   # "right" / "wrong" name corrections vs the reference
        wrong_examples: list[dict] = []
        for name, video, ref_path, ref_w, lyric_times in episodes:
            if kind == "cache":
                data = cached_transcript(video)
                if data is None:
                    print(f"{spec} {name}: no cached production transcript")
                    continue
            else:
                work = Path(args.work) / hashlib.sha1(rest.encode()).hexdigest()[:10]
                data = run_asr(video, overrides, work, None)
            segs = kept_segments(data)
            if post_names:
                episode = glossary_profile.find_episode(video.name)
                here, known = name_correction.episode_names(tvdb_id, episode, args.glossary_dir, cast_report)
                # The episode's own reference is excluded: in production the
                # episode being transcribed usually has no subtitle.
                vocab = name_correction.lowercase_vocabulary(
                    [w for seg in segs for w in seg.words],
                    name_correction.series_vocabulary(season.parent, args.lang, exclude=ref_path))
                for seg in segs:
                    seg.words = name_correction.correct_words(seg.words, here, known, vocab)
                    for w in seg.words:
                        if any(c.rule_id == "cast-name-one-edit" for c in w.corrections):
                            minute = int(((seg.start + seg.end) / 2) // WINDOW)
                            target = normalise(w.text)[0] if normalise(w.text) else ""
                            ref_tokens = ref_w.get(minute, []) + ref_w.get(minute - 1, []) + ref_w.get(minute + 1, [])
                            ok = any(t == target or (t.startswith(target[:len(target) - 0]) and len(t) - len(target) <= 4)
                                     for t in ref_tokens)
                            fixes["right" if ok else "wrong"] += 1
                            if not ok:
                                wrong_examples.append({"episode": name, "minute": minute, "heard": w.original_text,
                                                       "corrected": w.text, "segment": segment_text(seg),
                                                       "reference": " ".join(ref_w.get(minute, []))[:300]})
                            fixes[f"{w.original_text.strip(chr(39) + ',.!?')} -> {w.text.strip(chr(39) + ',.!?')}"] += 1
            lyric_windows = {int(t // WINDOW) for t, _ in lyric_times}
            # Transcript text inside lyric-only minutes belongs to the lyrics.
            hyp_all = windows(segs, lambda s: (s.start + s.end) / 2, segment_text)
            hyp_w = {k: v for k, v in hyp_all.items() if k in ref_w or k not in lyric_windows}
            for k in lyric_windows:
                ref_l = [w for t, txt in lyric_times if int(t // WINDOW) == k for w in normalise(txt)]
                sub_l, del_l, _, _ = align(ref_l, hyp_all.get(k, []) if k not in ref_w else [])
                lyric_words += len(ref_l)
                lyric_hit += len(ref_l) - sub_l - del_l
            r = score(ref_w, hyp_w, names)
            if r["wer"] < 5:
                print(f"{spec} {name}: WER {r['wer']:.1f}% -- reference looks machine-made; skipped")
                continue
            per_episode[name] = {k: round(r[k], 1) for k in ("wer", "subst", "missed", "extra", "cer")}
            for k, v in r["windows"].items():
                all_windows[(name, k)] = v
            tot.update(e=round(r["wer"] * r["ref_words"] / 100), n=r["ref_words"],
                       s=round(r["subst"] * r["ref_words"] / 100), d=round(r["missed"] * r["ref_words"] / 100),
                       i=round(r["extra"] * r["ref_words"] / 100))
            agg_subs.update(r["substitutions"])
            agg_ref.update(r["name_ref"])
            agg_hit.update(r["name_hit"])
        n = tot["n"] or 1
        res = {"system": spec, "episodes": per_episode, "wer": round(100 * tot["e"] / n, 1),
               "subst": round(100 * tot["s"] / n, 1), "missed": round(100 * tot["d"] / n, 1),
               "extra": round(100 * tot["i"] / n, 1),
               "name_recall": round(100 * sum(agg_hit.values()) / (sum(agg_ref.values()) or 1), 1),
               "lyrics_coverage": round(100 * lyric_hit / lyric_words, 1) if lyric_words else None,
               "name_corrections": {"right": fixes.pop("right", 0), "wrong": fixes.pop("wrong", 0),
                                    "most_common": fixes.most_common(15),
                                    "unconfirmed": wrong_examples} if post_names else None,
               "lyric_words": lyric_words,
               "names": {k: f"{agg_hit[k]}/{v}" for k, v in agg_ref.most_common()},
               "top_substitutions": [[a, b, c] for (a, b), c in agg_subs.most_common(args.top)],
               "worst_windows": sorted(([f"{e}@{k}min", v[0], v[1]] for (e, k), v in all_windows.items()
                                        if v[1] >= 10), key=lambda x: -x[1] / x[2])[:8],
               "seconds": round(time.time() - t0, 1), "_windows": all_windows}
        results.append(res)
        print(f"\n== {spec}: WER {res['wer']}% (wrong {res['subst']}, missed {res['missed']}, "
              f"extra {res['extra']}) | name recall {res['name_recall']}% | lyrics coverage "
              f"{res['lyrics_coverage']}% of {res['lyric_words']} words | {len(per_episode)} episodes")
        for ep, v in per_episode.items():
            print(f"   {ep}: WER {v['wer']}%  CER {v['cer']}%")
        print("   names (found/in reference):", ", ".join(f"{k} {v}" for k, v in list(res["names"].items())[:12]))
        print("   top substitutions:", ", ".join(f"{a}->{b} x{c}" for a, b, c in res["top_substitutions"][:10]))
        if res["name_corrections"]:
            nc = res["name_corrections"]
            print(f"   name corrections: {nc['right']} confirmed by the reference, {nc['wrong']} not; "
                  + ", ".join(f"{k} x{v}" for k, v in nc["most_common"][:10]))

    base = results[0] if results else None
    for res in results[1:]:
        lo, hi = bootstrap(base["_windows"], res["_windows"])
        res["delta_vs_first"] = round(res["wer"] - base["wer"], 2)
        res["delta_95ci"] = [round(lo, 2), round(hi, 2)]
        print(f"\n{res['system']} vs {base['system']}: WER {res['delta_vs_first']:+.2f} points, "
              f"95% CI [{lo:+.2f}, {hi:+.2f}]")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"season": str(season), "reference_language": args.lang,
             "metric": "WER vs human subtitles, 60s windows, normalised (see script docstring)",
             "results": [{k: v for k, v in r.items() if k != "_windows"} for r in results]},
            ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
