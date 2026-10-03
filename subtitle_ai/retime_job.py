"""One re-timing run for the worker: parse the subtitle, get the audio's word
times, align, and write the result to a scratch directory. The worker commits
it to the library with one atomic write, exactly as for the other job types.

The transcript is read from the transcript cache when the video already has
one (any ASR run of that video is audio-only), otherwise the audio is
transcribed here with production settings. Nothing is written to the cache, so
the "every job transcribes afresh" setting is unaffected.
See docs/decisions/2026-10-03-subtitle-retiming.md.
"""

from __future__ import annotations

import glob
import json
import re
from dataclasses import dataclass
from pathlib import Path

import retime
import srt
from retime import RetimeReport, TimedWord


@dataclass
class RetimeOutcome:
    srt_path: str | None          # None: already in step, nothing to write
    report: RetimeReport
    transcript: str               # "cache" or "fresh"


def language_from_filename(name: str) -> str | None:
    """`film.tr.srt` / `film.tr.retimed.srt` -> `tr`; None when the name has no language tag."""
    match = re.search(r"\.([a-z]{2,3})(?:\.retimed)?\.srt$", name)
    return match.group(1) if match else None


def cached_words(video: Path, cache_dir: str | None) -> list[TimedWord] | None:
    """Words of the newest cached transcript of `video`, or None."""
    if not cache_dir:
        return None
    best = None
    for name in glob.glob(f"{cache_dir}/*.json"):
        try:
            data = json.loads(Path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("media_path") == str(video) and (best is None or data.get("created_at", 0) > best.get("created_at", 0)):
            best = data
    if best is None:
        return None
    return [TimedWord(w["text"], w["start"], w["end"]) for s in best["segments"] if not s.get("suppressed")
            for w in s["words"]]


def transcribe_words(video: Path, work: Path, on_event=None) -> list[TimedWord]:
    """Audio-only transcription with production settings. Caller holds the GPU lock."""
    import asr
    import audio_streams
    import hallucination
    import media
    stream = audio_streams.recommend_stream(video, work / "stream_samples").recommended_index
    wav = work / "audio.wav"
    media.extract_audio(video, stream, wav)
    progress = (lambda pos, total, n: on_event("ASR_PROGRESS", {"position": pos, "total": total, "segments": n})
                if on_event else None)
    transcript = asr.transcribe(str(wav), str(video), media.content_fingerprint(video), stream, on_progress=progress)
    wav.unlink(missing_ok=True)
    hallucination.detect(transcript.segments, transcript.language)
    return [TimedWord(w.text, w.start, w.end) for s in transcript.segments if not getattr(s, "suppressed", False)
            for w in s.words]


def run_retime(video_path: str, source_srt_path: str, work_dir: str, language: str, *,
               transcript_cache_dir: str | None = None, on_event=None) -> RetimeOutcome:
    """Raises RetimeRefusedError when the evidence is too thin; writes
    `<work_dir>/retimed.srt` only when times actually change."""
    def emit(name: str, **data) -> None:
        if on_event:
            on_event(name, data)

    video = Path(video_path)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    emit("RETIME_STARTED")
    cues = srt.parse_lines(source_srt_path)
    emit("RETIME_PARSE_COMPLETED", cues=len(cues))
    words = cached_words(video, transcript_cache_dir)
    source = "cache"
    if words is None:
        source = "fresh"
        words = transcribe_words(video, work, on_event)
    else:
        emit("ASR_CACHE_HIT")
    emit("RETIME_TRANSCRIPT_READY", words=len(words), source=source)
    flat = [srt.SrtCue(c.start, c.end, " ".join(c.lines)) for c in cues]
    times, report = retime.retime(flat, words, language)
    emit("RETIME_ALIGNED", method=report.method, anchored=report.anchored_cues, cues=report.cues)
    if not report.changed:
        return RetimeOutcome(None, report, source)
    out = work / "retimed.srt"
    out.write_text(srt.render([srt.SrtCueLines(s, e, c.lines) for c, (s, e) in zip(cues, times)]), encoding="utf-8")
    return RetimeOutcome(str(out), report, source)
