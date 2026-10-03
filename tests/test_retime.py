import random
import unittest
from dataclasses import dataclass

import retime
from retime import RetimeRefused, TimedWord


@dataclass
class Cue:
    start: float
    end: float
    text: str


def make_programme(n=400, seed=3, language_words=600):
    """Ground-truth cues and the audio's words for them (words spread inside each cue)."""
    rng = random.Random(seed)
    vocab = [f"kelime{i}" for i in range(language_words)]
    cues, words, t = [], [], 5.0
    for _ in range(n):
        text = " ".join(rng.choice(vocab) for _ in range(rng.randint(4, 9)))
        duration = rng.uniform(1.5, 3.5)
        cues.append(Cue(t, t + duration, text))
        pieces = text.split()
        for k, w in enumerate(pieces):
            a = t + duration * k / len(pieces)
            words.append(TimedWord(w, a, a + duration / len(pieces)))
        t += duration + rng.uniform(0.3, 3.0)
    return cues, words


def distort(cues, fn):
    return [Cue(fn(c.start), fn(c.end), c.text) for c in cues]


def mean_error(truth, times):
    return sum(abs(a.start - s) + abs(a.end - e) for a, (s, e) in zip(truth, times)) / (2 * len(truth))


class RetimeTests(unittest.TestCase):
    def setUp(self):
        self.truth, self.words = make_programme()

    def check(self, fn, method, limit=0.2):
        times, report = retime.retime(distort(self.truth, fn), self.words, "tr")
        self.assertEqual(report.method, method)
        self.assertLess(mean_error(self.truth, times), limit)
        return report

    def test_constant_shift_late(self):
        report = self.check(lambda t: t + 5.1, "constant offset")
        self.assertAlmostEqual(report.pieces[0].offset0, -5.1, delta=0.1)

    def test_constant_shift_early(self):
        self.check(lambda t: t - 3.4, "constant offset")

    def test_frame_rate_drift(self):
        # 25 fps subtitle against 23.976 fps audio
        report = self.check(lambda t: t * 23.976 / 25, "linear drift")
        self.assertEqual(len(report.pieces), 1)

    def test_step_from_a_cut(self):
        mid = self.truth[len(self.truth) // 2].start
        report = self.check(lambda t: t + (4.0 if t > mid else 0.0), "stepped")
        self.assertEqual(len(report.pieces), 2)

    def test_two_steps(self):
        a, b = self.truth[130].start, self.truth[270].start
        self.check(lambda t: t + (0 if t < a else 2.5 if t < b else -1.5), "stepped", limit=0.4)

    def test_already_aligned_changes_nothing(self):
        times, report = retime.retime(self.truth, self.words, "tr")
        self.assertEqual(report.method, "aligned")
        self.assertFalse(report.changed)
        self.assertEqual(times, [(c.start, c.end) for c in self.truth])

    def test_text_is_never_part_of_the_result(self):
        times, _ = retime.retime(distort(self.truth, lambda t: t + 5), self.words, "tr")
        self.assertEqual(len(times), len(self.truth))

    def test_noisy_cue_times_still_fit(self):
        rng = random.Random(9)
        noisy = [Cue(c.start + 5 + rng.uniform(-.4, .4), c.end + 5 + rng.uniform(-.4, .4), c.text) for c in self.truth]
        times, report = retime.retime(noisy, self.words, "tr")
        self.assertEqual(report.method, "constant offset")
        self.assertLess(mean_error(self.truth, times), 0.5)

    def test_survives_wrong_matches(self):
        rng = random.Random(4)
        cues = distort(self.truth, lambda t: t + 5.1)
        for c in rng.sample(cues, 30):          # 7% of cues carry other text
            c.text = " ".join(f"zzz{rng.randint(0, 99)}" for _ in range(6))
        times, report = retime.retime(cues, self.words, "tr")
        self.assertEqual(report.method, "constant offset")
        good = [a for a, c in zip(self.truth, cues) if not c.text.startswith("zzz")]
        kept = [t for t, c in zip(times, cues) if not c.text.startswith("zzz")]
        self.assertLess(mean_error(good, kept), 0.2)

    def test_refuses_a_different_programme(self):
        _, other_words = make_programme(seed=99)
        other = [TimedWord(f"baska{i % 700}", w.start, w.end) for i, w in enumerate(other_words)]
        with self.assertRaises(RetimeRefused):
            retime.retime(distort(self.truth, lambda t: t + 5), other, "tr")

    def test_refuses_nonsense_disagreement(self):
        cues = distort(self.truth, lambda t: t + 3)
        # alternate stretches of the programme disagree wildly -> no single story
        for i, c in enumerate(cues):
            if i % 2:
                c.start += 40 * ((i // 2) % 3)
                c.end += 40 * ((i // 2) % 3)
        with self.assertRaises(RetimeRefused):
            retime.retime(cues, self.words, "tr")

    def test_cues_never_start_before_zero_or_overlap_after_a_step(self):
        mid = self.truth[200].start
        times, _ = retime.retime(distort(self.truth, lambda t: t + (-4.0 if t > mid else 3.0)), self.words, "tr")
        self.assertTrue(all(s >= 0 for s, _ in times))
        for (s0, e0), (s1, _) in zip(times, times[1:]):
            self.assertLessEqual(e0, s1 + 1e-9)


class TokenTests(unittest.TestCase):
    def test_spaced_language_splits_words_and_drops_tags(self):
        self.assertEqual(retime.tokens("<i>Merhaba, DÜNYA!</i> (müzik)", "tr"), ["merhaba", "dünya"])

    def test_unspaced_language_is_per_character(self):
        self.assertEqual(retime.tokens("你好，世界！", "zh"), ["你", "好", "世", "界"])

    def test_thai_marks_are_kept(self):
        self.assertEqual(len(retime.tokens("สวัสดี", "th")), 6)

    def test_unspaced_needs_longer_runs_to_anchor(self):
        cues = [Cue(10.0, 12.0, "你好世界")]
        words = [TimedWord(c, 20 + i * .5, 20.4 + i * .5) for i, c in enumerate("你好世界")]
        self.assertEqual(retime.find_anchors(cues, words, "zh"), [])   # 4 characters < 6


if __name__ == "__main__":
    unittest.main()
