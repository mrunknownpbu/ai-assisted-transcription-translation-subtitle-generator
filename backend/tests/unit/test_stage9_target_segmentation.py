import pytest

from app.pipeline.interfaces import ASRWord, CanonicalSegment, CanonicalTranscript, TranslatedChunk
from app.pipeline.stage9_target_segmentation.chunker import group_into_source_chunks
from app.pipeline.stage9_target_segmentation.segmenter import segment_into_cue_drafts


def _segment(seg_id, start, end, text, suppressed=False):
    words = [ASRWord(w, start + i * 0.1, start + (i + 1) * 0.1, 0.9) for i, w in enumerate(text.split())]
    return CanonicalSegment(segment_id=seg_id, start=start, end=end, text=text, normalized_text=text,
                             words=words, avg_confidence=0.9, suppressed=suppressed)


# --- chunker ---

@pytest.mark.unit
def test_pause_gap_splits_into_separate_chunks():
    segments = [
        _segment("s1", 0.0, 1.0, "hello there"),
        _segment("s2", 3.0, 4.0, "goodbye now"),  # 2s gap >= default pause threshold
    ]
    transcript = CanonicalTranscript(segments=segments, provenance={})

    chunks = group_into_source_chunks(transcript, pause_gap_s=0.7)

    assert len(chunks) == 2
    assert chunks[0].source_text == "hello there"
    assert chunks[1].source_text == "goodbye now"


@pytest.mark.unit
def test_small_gap_merges_into_one_chunk():
    segments = [
        _segment("s1", 0.0, 1.0, "hello there"),
        _segment("s2", 1.2, 2.0, "friend"),  # 0.2s gap < threshold
    ]
    transcript = CanonicalTranscript(segments=segments, provenance={})

    chunks = group_into_source_chunks(transcript, pause_gap_s=0.7)

    assert len(chunks) == 1
    assert chunks[0].source_text == "hello there friend"
    assert chunks[0].source_segment_ids == ["s1", "s2"]


@pytest.mark.unit
def test_suppressed_segments_are_excluded_from_chunks():
    segments = [
        _segment("s1", 0.0, 1.0, "real dialogue"),
        _segment("s2", 1.1, 2.0, "phantom hallucination", suppressed=True),
        _segment("s3", 2.1, 3.0, "more real speech"),
    ]
    transcript = CanonicalTranscript(segments=segments, provenance={})

    chunks = group_into_source_chunks(transcript, pause_gap_s=0.7)
    all_text = " ".join(c.source_text for c in chunks)

    assert "phantom" not in all_text
    assert "real dialogue" in all_text
    assert "more real speech" in all_text


@pytest.mark.unit
def test_empty_transcript_produces_no_chunks():
    transcript = CanonicalTranscript(segments=[], provenance={})
    assert group_into_source_chunks(transcript, pause_gap_s=0.7) == []


@pytest.mark.unit
def test_chunk_breaks_on_char_budget_even_without_a_pause_gap():
    """A long dialogue run with only short pauses must not grow one SourceChunk past the
    translation engine's practical input length — this is what prevents silent tokenizer
    truncation of the overflow. Each individual segment here is small relative to the
    budget, so only accumulation across segments should trigger a break."""
    segments = [_segment(f"s{i}", float(i), float(i) + 0.9, "hi") for i in range(10)]  # tight pauses (0.1s), 2 chars each
    transcript = CanonicalTranscript(segments=segments, provenance={})

    chunks = group_into_source_chunks(transcript, pause_gap_s=0.7, max_chunk_chars=15, max_chunk_segments=100)

    assert len(chunks) > 1
    assert all(len(c.source_text) <= 15 for c in chunks)


@pytest.mark.unit
def test_chunk_breaks_on_segment_count_budget():
    segments = [_segment(f"s{i}", float(i), float(i) + 0.9, "hi") for i in range(10)]  # tight pauses
    transcript = CanonicalTranscript(segments=segments, provenance={})

    chunks = group_into_source_chunks(transcript, pause_gap_s=0.7, max_chunk_chars=10_000, max_chunk_segments=3)

    assert len(chunks) == 4  # 10 segments / 3 per chunk, rounded up
    assert all(len(c.source_segment_ids) <= 3 for c in chunks)


@pytest.mark.unit
def test_no_text_is_lost_when_chunks_split_on_budget():
    segments = [_segment(f"s{i}", float(i), float(i) + 0.9, f"word{i}") for i in range(8)]
    transcript = CanonicalTranscript(segments=segments, provenance={})

    chunks = group_into_source_chunks(transcript, pause_gap_s=0.7, max_chunk_chars=15, max_chunk_segments=100)

    all_words = " ".join(c.source_text for c in chunks).split()
    assert all_words == [f"word{i}" for i in range(8)]


# --- segmenter ---

def _translated_chunk(chunk_id, text, span=(0.0, 2.0)):
    return TranslatedChunk(chunk_id=chunk_id, target_language="es", source_text=text, translated_text=text,
                            engine="fake", model_version="fake:1", word_span=span)


@pytest.mark.unit
def test_short_chunk_becomes_single_draft():
    chunk = _translated_chunk("c1", "Hola amigo")
    drafts = segment_into_cue_drafts([chunk], max_chars_per_line=42, max_lines_per_cue=2)
    assert len(drafts) == 1
    assert drafts[0].source_chunk_ids == ["c1"]
    assert drafts[0].char_share_of_chunks == {"c1": 1.0}


@pytest.mark.unit
def test_long_chunk_splits_into_multiple_drafts_summing_shares_to_one():
    long_text = ("This is a very long translated sentence that will not fit into a single "
                 "subtitle cue and must be split into several pieces for readability. "
                 "Here is another sentence to make it even longer than before.")
    chunk = _translated_chunk("c_long", long_text)

    drafts = segment_into_cue_drafts([chunk], max_chars_per_line=42, max_lines_per_cue=2)

    assert len(drafts) > 1  # N:M mapping: one source chunk -> many cues
    for d in drafts:
        assert d.source_chunk_ids == ["c_long"]
        for line in d.text.split("\n"):
            assert len(line) <= 42 + 15  # soft budget; leftover-word appending can slightly exceed
    total_share = sum(d.char_share_of_chunks["c_long"] for d in drafts)
    assert total_share == pytest.approx(1.0, abs=0.01)


@pytest.mark.unit
def test_two_short_adjacent_chunks_merge_into_one_cue():
    chunk_a = _translated_chunk("c1", "Hola")
    chunk_b = _translated_chunk("c2", "amigo mio")

    drafts = segment_into_cue_drafts([chunk_a, chunk_b], max_chars_per_line=42, max_lines_per_cue=2)

    assert len(drafts) == 1  # N:M mapping: many source chunks -> one cue
    assert set(drafts[0].source_chunk_ids) == {"c1", "c2"}
    assert "Hola" in drafts[0].text and "amigo mio" in drafts[0].text


@pytest.mark.unit
def test_short_adjacent_chunks_do_not_merge_across_a_real_silence():
    """Regression test for a real production bug found via the translation-accuracy
    evaluation harness: a short chunk ("You shook me", a song lyric) and a short chunk 46
    seconds later ("Subtitles group", a fansub-credit watermark) were merged into one cue
    purely because both were short TEXT, with no check on the gap between them -- Stage
    10's union-of-spans timing for multi-chunk drafts then stretched that one cue across
    the entire 46-second silence between them."""
    chunk_a = _translated_chunk("c1", "You shook me", span=(2203.7, 2206.8))
    chunk_b = _translated_chunk("c2", "Subtitles group", span=(2253.3, 2255.1))

    drafts = segment_into_cue_drafts([chunk_a, chunk_b], max_chars_per_line=42, max_lines_per_cue=2)

    assert len(drafts) == 2  # never merged -- the real gap between them is too large
    assert drafts[0].source_chunk_ids == ["c1"]
    assert drafts[1].source_chunk_ids == ["c2"]


@pytest.mark.unit
def test_short_adjacent_chunks_still_merge_within_a_real_pause_gap():
    chunk_a = _translated_chunk("c1", "Hola", span=(0.0, 1.0))
    chunk_b = _translated_chunk("c2", "amigo mio", span=(1.3, 2.0))  # 0.3s gap -- a normal breath, not a scene break

    drafts = segment_into_cue_drafts([chunk_a, chunk_b], max_chars_per_line=42, max_lines_per_cue=2,
                                      max_merge_gap_s=1.0)

    assert len(drafts) == 1
    assert set(drafts[0].source_chunk_ids) == {"c1", "c2"}


@pytest.mark.unit
def test_empty_chunk_list_produces_no_drafts():
    assert segment_into_cue_drafts([], max_chars_per_line=42, max_lines_per_cue=2) == []


@pytest.mark.unit
def test_long_chunk_never_leaves_a_lone_trailing_word_as_its_own_draft():
    """Regression test for a real production bug found via the translation-accuracy
    evaluation harness: a 45-minute episode's output isolated the word "day." from the
    sentence "...thinking about being a pilot like you do every day." into its own draft,
    which Stage 10 then gave an almost-zero, floored-up-to-the-minimum duration -- a
    single word flashing by for 0.8s right after the sentence it belongs to."""
    long_text = ("This is a very long sentence that keeps going on for quite a while to "
                 "fill up the character budget nicely and thoroughly indeed. "
                 "It's better than quitting early and thinking about being a pilot like "
                 "you do every day.")
    chunk = _translated_chunk("c_long", long_text)

    drafts = segment_into_cue_drafts([chunk], max_chars_per_line=42, max_lines_per_cue=2)

    assert len(drafts) > 1  # still splits -- the whole text is too long for one cue
    for d in drafts:
        word_count = len(d.text.split())
        # A one- or two-word trailing draft is exactly the degenerate case: it would get an
        # almost-zero proportional share of the source chunk's real spoken duration.
        assert word_count > 2, f"draft {d.draft_id!r} is too short to stand alone: {d.text!r}"
    assert "day" in drafts[-1].text  # merged into the previous piece, not dropped


@pytest.mark.unit
def test_long_multi_segment_chunk_still_splits_for_readability():
    """A chunk merging several original ASR segments must still split like any other
    over-budget chunk. An earlier fix refused to split such chunks at all (to avoid
    mistiming a fragment when translation reorders clauses across merged segments -- a
    real but narrow case found via the translation-accuracy evaluation harness), keeping
    them as one cue over the correct full span. On a real 39-episode-scale job that made
    things far worse: 442 QC line-length/reading-speed failures in a single episode, since
    every long multi-segment chunk became one massive, unreadable cue (over-length lines
    up to 258 chars against a 42-char max). Splitting normally -- accepting that a split
    piece can occasionally land a few seconds off within its own chunk's span in the rare
    reordering case -- is the better empirical tradeoff over an unreadable wall of text on
    nearly every long chunk."""
    long_text = ("Don't worry, I'll tell you how many people can be affected by the "
                 "cancellation of a flight program.")
    chunk = _translated_chunk("c_multi", long_text, span=(1440.87, 1447.55))

    drafts = segment_into_cue_drafts([chunk], max_chars_per_line=42, max_lines_per_cue=2)

    assert len(drafts) > 1  # splits like any other over-budget chunk
    all_text = " ".join(d.text for d in drafts)
    assert "flight program" in all_text
    assert "Don't worry" in all_text  # nothing dropped, just re-cut


@pytest.mark.unit
def test_long_chunk_with_multiple_short_sentences_never_leaves_a_tiny_trailing_sentence():
    long_text = ("This is a reasonably long first sentence that takes up a good chunk of "
                 "the available character budget already. Ok.")
    chunk = _translated_chunk("c_long", long_text)

    drafts = segment_into_cue_drafts([chunk], max_chars_per_line=42, max_lines_per_cue=2)

    assert len(drafts) > 1
    assert len(drafts[-1].text) >= 12, f"trailing draft too small: {drafts[-1].text!r}"
    assert "Ok" in drafts[-1].text
