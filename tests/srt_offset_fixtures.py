"""Deterministic synthetic reference/candidate SRT pairs for timing-offset tests.

Stand-in for the real human/AI pair behind the reported ~5.1 s offset
(docs/handover.md), which is not in this checkout. The reference mimics an
episode: ~2-4 s cues separated by irregular gaps, every cue's text unique.
Candidates are built from it so the true offset is known exactly.
"""
from __future__ import annotations

import random

import srt

OFFSET = 5.1
_WORDS = ("harbor lantern wander silver marble copper velvet thunder meadow ember cobalt "
          "willow granite saffron orchid pewter juniper falcon mosaic quartz bramble tundra "
          "cascade nectar rustle zephyr gossamer lagoon sparrow thistle ripple mirage onyx "
          "dune fable ivory kestrel lilac nimbus oasis prism quill raven sable talon umber "
          "vesper wren yarrow zenith amber basalt cinder drift").split()


def reference_cues(n: int = 60, seed: int = 7) -> list[srt.SrtCue]:
    rng = random.Random(seed)
    cues, t = [], 3.0
    for i in range(n):
        duration = rng.uniform(1.8, 4.2)
        words = [_WORDS[(i * 5 + k * 3) % len(_WORDS)] + str(i) for k in range(rng.randint(4, 9))]
        cues.append(srt.SrtCue(start=round(t, 3), end=round(t + duration, 3),
                            text=" ".join(words)))
        t += duration + rng.uniform(0.2, 3.5)
    return cues


def shifted(cues, offset: float):
    return [srt.SrtCue(start=round(c.start + offset, 3), end=round(c.end + offset, 3),
                    text=c.text) for c in cues]


def resegmented(cues, offset: float, seed: int = 11):
    """Same speech, different cue boundaries and small timing jitter: some
    cues split in two at a word boundary, some adjacent pairs merged."""
    rng = random.Random(seed)
    out, i = [], 0
    while i < len(cues):
        c = cues[i]
        roll = rng.random()
        words = c.text.split()
        if roll < 0.3 and len(words) >= 4:
            cut = len(words) // 2
            mid = c.start + (c.end - c.start) * cut / len(words)
            out += [(c.start, mid, " ".join(words[:cut])), (mid, c.end, " ".join(words[cut:]))]
        elif roll < 0.5 and i + 1 < len(cues) and cues[i + 1].start - c.end < 1.0:
            n = cues[i + 1]
            out.append((c.start, n.end, c.text + " " + n.text))
            i += 1
        else:
            out.append((c.start, c.end, c.text))
        i += 1
    return [srt.SrtCue(start=round(s + offset + rng.uniform(-0.15, 0.15), 3),
                    end=round(e + offset + rng.uniform(-0.15, 0.15), 3), text=t)
            for k, (s, e, t) in enumerate(out)]


def stepped(cues, offset: float):
    """Offset appears only from the midpoint on (e.g. a gap the decoder
    skipped): not a constant shift."""
    half = len(cues) // 2
    return [srt.SrtCue(start=round(c.start + (offset if k >= half else 0), 3),
                    end=round(c.end + (offset if k >= half else 0), 3), text=c.text)
            for k, c in enumerate(cues)]


def drifting(cues, ppm_fraction: float = 0.002):
    return [srt.SrtCue(start=round(c.start * (1 + ppm_fraction), 3),
                    end=round(c.end * (1 + ppm_fraction), 3), text=c.text) for c in cues]
