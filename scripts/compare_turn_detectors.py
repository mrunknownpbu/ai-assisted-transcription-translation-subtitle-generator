#!/usr/bin/env python3
"""Compare turns.heuristic_turns vs turns.voice_turns against the human
two-speaker "- A / - B" cues of the same episode -- natural-dialogue plan
step 3's "let measurements choose" comparison. Reuses eval_transcription.py's
own cached-transcript/reference-skip/dash-turn-point logic rather than
reimplementing it.

Run inside the app container (needs the WeSpeaker ONNX model for the
voice detector -- scripts/download_speaker_model.py):

    docker exec -e PYTHONPATH=/app subtitle-ai python /tmp/compare_turn_detectors.py \\
        --season "/data/media/.../Season 01" --episodes 1 --json /tmp/turns-compare.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_spec = importlib.util.spec_from_file_location("eval_transcription", Path(__file__).resolve().parent / "eval_transcription.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def _turn_word_times(words, indices: set[int]) -> list[float]:
    return [words[i].start for i in sorted(indices) if 0 <= i < len(words)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True)
    ap.add_argument("--episodes", default="1")
    ap.add_argument("--lang", default="tr")
    ap.add_argument("--json")
    args = ap.parse_args()

    import normalize
    import turns
    from srt import parse_lines

    season = Path(args.season)
    results = []
    for number in ev.parse_episodes(args.episodes):
        video = next(iter(sorted(season.glob(f"*E{number:02d}.mkv"))), None)
        ref = next(iter(season.glob(f"*E{number:02d}.{args.lang}.srt")), None)
        if not video or not ref or ev.app_written(ref):
            print(f"E{number:02d}: skipped (no video/ref, or app-written reference)")
            continue
        data = ev.cached_transcript(video)
        if data is None:
            print(f"E{number:02d}: no cached production transcript, skipped")
            continue
        segs = ev.kept_segments(data)
        live_words = [w for s in segs for w in normalize.normalize_transcript_words(list(s.words), data.get("language", ""))]
        ref_lines = [c for c in parse_lines(ref) if not ev.is_lyric(" ".join(c.lines))]
        dash_points = ev.dash_turn_points(ref_lines)
        print(f"E{number:02d}: {len(live_words)} words, {len(dash_points)} human turn points")

        episode_result = {"episode": f"E{number:02d}", "human_turn_points": len(dash_points), "detectors": {}}
        for mode in ("heuristic", "voice"):
            t0 = time.time()
            try:
                if mode == "voice":
                    wav_path = str(Path(video).with_suffix(".wav"))
                    if not Path(wav_path).is_file():
                        # Extract once for this comparison (not written back to the library).
                        import media
                        media.extract_audio(video, data.get("audio_stream_index", 0), Path(wav_path))
                    indices = turns.voice_turns(live_words, wav_path)
                else:
                    indices = turns.heuristic_turns(live_words)
            except Exception as exc:
                print(f"  {mode}: FAILED ({exc})")
                episode_result["detectors"][mode] = {"error": str(exc)}
                continue
            seconds = time.time() - t0
            turn_times = _turn_word_times(live_words, indices)
            tp, ref_n, hyp_n = ev.match_points(dash_points, turn_times, tol=0.6)
            p, r, f1 = ev.prf(tp, ref_n, hyp_n)
            print(f"  {mode}: {len(indices)} turns detected, recall {100*r:.1f}%, "
                 f"precision {100*p:.1f}%, F1 {100*f1:.1f}%, {seconds:.1f}s")
            episode_result["detectors"][mode] = {
                "turns_detected": len(indices), "recall": round(100 * r, 1),
                "precision": round(100 * p, 1), "f1": round(100 * f1, 1), "seconds": round(seconds, 1)}
        results.append(episode_result)

    if args.json:
        Path(args.json).write_text(json.dumps({"season": str(season), "results": results}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
