"""Auto-mines a series' own already-completed sibling episode outputs
for recurring proper names, so a per-series glossary doesn't have to be
hand-authored from scratch (see /glossary/*.yaml, glossary_profile.py).

Confirmed real need (Love Is In The Air audit, 2026-09-17): the manually
curated glossary for that series was built by eyeballing one episode's
transcript and picking out recurring names by hand -- a one-off chore
that has to be redone per series and misses names that only become
obviously recurring once several episodes exist.

Safety framing (deliberate, load-bearing design decision): names found
here are SUGGESTIONS for human review in the Series page. They feed neither
ASR (an existing subtitle must not influence Workflow A's reading of the
audio) nor translation-time protect()/restore() (glossary.build_glossary()).
A false-positive PROTECTED entity would silently corrupt genuine dialogue
translation with no human review. Promoting a mined name to actual
translation protection is a human decision, made by copying it from
this module's write_suggestions() output into the real, hand-curated
/glossary YAML (which stays the only path to protect()/restore()).

Per-language mining (2026-09-29): v1 was Turkish-source-only, with a
single hardcoded capitalization pattern. Real orthographic signal
differs by script, so each language below is a separate LanguageMiner
registered in MINERS, not one pattern stretched to fit every script:
- tr, ms: Latin capitalization (Turkish/Malay orthography capitalizes
  proper nouns like English does) -- a sentence-initial capital is a
  confound (ordinary sentence-initial capitalization looks identical),
  resolved the same way as before: a candidate only counts once also
  seen capitalized somewhere NOT cue-initial ("corroboration").
- ja: katakana script-switching. Japanese has no capitalization at all,
  but katakana (vs. the default hiragana/kanji prose) is a real,
  distinct-script signal genuinely used for foreign names, loanwords,
  and stylized native names -- confirmed against real production data
  (2026-09-29, "Happy Kanako's Killer Life" S02E01-03 + "Hammer
  Session!" S01E01, the only real .ja.srt transcripts in this
  deployment): correctly surfaces real character names (カナコ/Kanako,
  カズ/Kazu, ユイ/Yui) and, notably, an agency name (スマイル/"Smile")
  that a real mistranslation bug traced back to -- see CLAUDE.md's dated
  entry. No script-based confound exists the way sentence-initial
  capitalization does, so every occurrence self-corroborates (position
  doesn't matter).
- ko: word-spaced, but no capitalization AND no distinct-script signal
  (unlike ja) -- see the module's ko section below for the real
  cross-series-overlap measurement this one required before it was safe
  to ship, and its disclosed lower precision than tr/ms/ja.
- zh: no word spacing AND no capitalization-equivalent marker -- harder
  still than ko's. Candidates are character n-grams over a sliding
  window (no real word boundaries to split on), which independently
  produces redundant overlapping fragments of the same real term (see
  _collapse_substring_redundant_zh_candidates()); the same cross-series-
  overlap technique validated for ko discriminates real recurring terms
  from common function words here too -- see the module's zh section
  below for the real measurement.
- th: same two problems as Chinese (no word spacing, no capitalization-
  equivalent marker) plus a third -- Thai has no reliable syllable/
  character-count-based candidate boundary the way Hanzi's "2-4
  characters" heuristic gives Chinese, since Thai script combines base
  consonants with vowel/tone marks that can appear before, after, above,
  or below the consonant they belong to. See the module's th section
  below for the real cross-series measurement and how candidate
  extraction handles this.

mine_series_entities()/mine_series_entities_auto() both degrade to []
for any unregistered language, same as today's "no match of any kind"
behavior -- add a MINERS entry (see LanguageMiner's docstring) once real
transcribed episodes exist to measure a heuristic against, the same
discipline every other threshold in this codebase was built with (the
reason none of tr/ms/ja/ko/zh above skipped that step, and why th
wasn't attempted until real Thai embedded-subtitle-stream source data
was actually confirmed present in this deployment, 2026-09-29).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import yaml

import glossary_files
import srt

SOURCE_LANG = "tr"

# ---------------------------------------------------------------------------
# Turkish (tr): Latin capitalization.
# ---------------------------------------------------------------------------

# Matches faster-whisper's own Turkish capitalization behavior for
# proper nouns: a leading uppercase letter (ASCII or Turkish-specific),
# rest lowercase. ALL-CAPS is excluded on purpose -- Whisper occasionally
# emits emphasis/acronym-shaped artifacts in all-caps, which is never a
# genuine name.
_LATIN_PROPER_NOUN_TR = re.compile(r"^[A-ZÇĞİÖŞÜ][a-zçğıöşü]+$")

# Turkish orthography attaches grammatical suffixes to proper nouns with
# an apostrophe (Eda'yı, Eda'ya, Serkan'ın) -- stripped before counting
# so inflected mentions of the same name merge into one candidate
# instead of each inflection diluting its own count below threshold.
_SUFFIX_SPLIT = re.compile(r"['’].*$")

# Punctuation that can cling to a token after naive whitespace-splitting
# (sentence-final periods, commas, quote marks) -- stripped from both
# ends before pattern-matching, since e.g. "Eda." must still match.
_STRIP_CHARS = ".,!?;:\"'’()[]{}—–"

# Sentence-initial common words get capitalized by Turkish orthography
# exactly like real names do, so capitalization alone isn't enough
# signal. Two categories deliberately included:
#  - pronouns/interjections/acknowledgements (the obvious noise)
#  - kinship/honorific address terms (Anne, Baba, Abla, Abi, Teyze,
#    Amca, Hoca, Doktor, Bey, Hanım) -- in Turkish dialogue these are
#    used AS a form of direct address ("Anne, gel!") and so recur across
#    every episode of any Turkish-language series, not just this one;
#    without this they would pass both thresholds trivially and become
#    permanent false candidates.
STOPWORDS_TR = frozenset({
    "Ben", "Sen", "O", "Biz", "Siz", "Onlar", "Bu", "Şu",
    "Tamam", "Evet", "Hayır", "Peki", "Hadi", "Haydi", "Bak", "Dur",
    "Şey", "Neden", "Nasıl", "Ne", "Kim", "Nerede", "Ama", "Fakat",
    "Yani", "Şimdi", "Sonra", "Önce", "Belki", "Aslında", "Tabii",
    "Lütfen", "Pardon", "Hey", "Vay",
    "Anne", "Baba", "Abla", "Abi", "Teyze", "Amca", "Dayı", "Hala",
    "Hoca", "Doktor", "Bey", "Hanım", "Efendim",
    # Added 2026-09-20 from a real Season 01 (Love Is In The Air,
    # tvdb-383383) full-corpus mining run: these ordinary Turkish
    # function words/interjections were crowding real recurring
    # character names (Pırıl, Aydan, Ayfer, Erdem, Deniz, ...) out of
    # TOP_N_CANDIDATES. They pass the corroborated-mid-cue check (which
    # exists precisely to reject sentence-initial-only capitalization,
    # see test_sentence_initial_only_capitalization_not_counted_as_proper_noun)
    # not because they're genuine proper nouns, but because faster-
    # whisper's Turkish casing is imperfect and sometimes capitalizes
    # them mid-cue too -- a real, observed ASR noise floor, not a gap in
    # the position-based heuristic itself.
    "Bir", "Çok", "Yok", "İyi", "Öyle", "Aa", "Her", "Çünkü", "Ay",
    "Allah", "Bana", "Gerçekten", "Senin", "Bence", "Böyle", "Sana",
    "Gel", "Hiç", "Hem", "Niye", "Vallahi", "Güzel", "Seni", "Benim",
    "Ya", "Ee", "Beni", "Zaten", "Neyse", "Daha", "Ve", "Biraz", "Olur",
    "Bunu", "Teşekkürler", "Teşekkür", "Ha", "Eğer", "İşte", "En",
    "Bizim",
})

# A cue where every word is capitalized is never ordinary dialogue -- real
# example (Season 01, mostly S01E01-E05): faster-whisper transcribes the
# show's sung opening theme in Title Case ("Yanlışlarımdan Ders Alacak
# Kadar Olgun Değilim..."), unlike its normal sentence-case dialogue
# output. Every word in a cue like that matches the proper-noun pattern and
# most aren't cue-initial, so lyrics were mass-corroborating ordinary
# words as "proper nouns" and drowning out real character names. 4+ words
# keeps this from misfiring on a short, genuinely all-capitalized
# two/three-word dialogue line (rare, but "İyi Akşamlar" style greetings
# exist) that happens to have no lowercase word to contrast against.
_TITLE_CASE_MIN_WORDS = 4


def _is_latin_title_case_cue(text: str) -> bool:
    words = [t for t in (raw.strip(_STRIP_CHARS) for raw in text.split()) if t]
    return len(words) >= _TITLE_CASE_MIN_WORDS and all(w[:1].isupper() for w in words)


def _latin_extract_tokens(pattern: re.Pattern, stopwords: frozenset[str], *,
                          strip_suffix: bool) -> Callable[[str], Iterable[tuple[str, bool]]]:
    """Builds a Latin-capitalization extractor for a `pattern`/`stopwords`
    pair -- shared by every Latin-script language (tr, ms) so the actual
    scanning logic (whitespace-split, strip clinging punctuation, position
    for the corroboration check) is written once. `strip_suffix` applies
    Turkish's apostrophe-suffix stripping (Eda'yı -> Eda); languages
    without that orthographic convention (Malay) pass False."""
    def extract(text: str) -> Iterable[tuple[str, bool]]:
        for i, raw in enumerate(text.split()):
            token = raw.strip(_STRIP_CHARS)
            if strip_suffix:
                token = _SUFFIX_SPLIT.sub("", token)
            if pattern.match(token) and token not in stopwords:
                yield token, i == 0
    return extract


# ---------------------------------------------------------------------------
# Malay (ms): Latin capitalization, same mechanism as Turkish.
#
# Standard Malay orthography capitalizes proper nouns exactly like
# English/Turkish does, over the same plain Latin A-Z alphabet (no
# Turkish-style diacritic letters). UNVALIDATED against real Malay
# dialogue (checked 2026-09-29: zero real .ms.srt transcripts exist
# anywhere in this deployment's library yet) -- STOPWORDS_MS below is a
# starter list built from established Malay grammar (pronouns,
# question words, common interjections, kinship/honorific address
# terms -- the same categories STOPWORDS_TR covers), not yet refined
# against a real mining run's false positives the way STOPWORDS_TR's
# 2026-09-20 addition was. Expect to extend this the first time a real
# Malay series is mined and produces noisy candidates, same as Turkish's
# own history.
# ---------------------------------------------------------------------------

_LATIN_PROPER_NOUN_MS = re.compile(r"^[A-Z][a-z]+$")

STOPWORDS_MS = frozenset({
    "Saya", "Aku", "Kau", "Kamu", "Awak", "Dia", "Kami", "Kita", "Mereka",
    "Ini", "Itu",
    "Ya", "Tidak", "Tak", "Baik", "Jangan", "Boleh", "Tolong",
    "Apa", "Siapa", "Kenapa", "Mengapa", "Bagaimana", "Bila", "Mana",
    "Sudah", "Dah", "Belum", "Nanti", "Sekarang", "Tadi",
    "Kalau", "Jika", "Tapi", "Tetapi", "Jadi", "Memang", "Betul",
    "Wah", "Aduh", "Aduhai", "Alamak", "Eh", "Hei", "Oh", "Ya lah",
    "Mak", "Emak", "Ibu", "Ayah", "Bapa", "Kakak", "Kak", "Abang", "Bang",
    "Adik", "Dik", "Datuk", "Nenek", "Pakcik", "Makcik",
    "Encik", "Puan", "Cik", "Tuan", "Doktor", "Cikgu",
})


# ---------------------------------------------------------------------------
# Japanese (ja): katakana script-switching, not capitalization.
#
# Japanese prose is hiragana/kanji by default; a run of katakana
# characters is a genuine, distinct-script signal used for foreign
# names, loanwords, and stylized native names -- there's no sentence-
# initial-capitalization confound the way Latin scripts have, so every
# occurrence self-corroborates (is_initial is always reported False).
# ---------------------------------------------------------------------------

# Full-width katakana block (U+30A1-U+30FA) plus the prolonged sound
# mark U+30FC ("ー") -- 2+ characters, since a single kana is too
# ambiguous (common particles/onomatopoeia fragments use katakana too).
_KATAKANA_RUN = re.compile(r"[ァ-ヺー]{2,}")

# Some Japanese caption/AD-style sources prefix a cue with the speaking
# character's name in half-width katakana parentheses, e.g. "(ｶﾅｺ)ｳｰﾝ..."
# -- confirmed real content (2026-09-29 production audit: genuinely
# spoken audio, not an ASR artifact -- see CLAUDE.md), but it's a
# caption/attribution convention, not dialogue text, and stripping it
# avoids mining the same handful of speaker labels as if they were
# newly-recurring dialogue words.
_BRACKET_SPEAKER_PREFIX = re.compile(r"^[（(][ｦ-ﾝァ-ヺー]+[)）]")

# Measured 2026-09-29 against the only real .ja.srt transcripts in this
# deployment ("Happy Kanako's Killer Life" S02E01-03, "Hammer Session!"
# S01E01): common katakana loanwords/interjections that recur often
# enough to otherwise qualify but are ordinary vocabulary, not names --
# same empirical-refinement pattern as STOPWORDS_TR's 2026-09-20 entry.
STOPWORDS_JA = frozenset({
    "マジ", "バカ", "カッコ", "ダメ", "ターゲット", "サイレンサー", "バイク",
    "オッケー", "ホント", "クビ", "マフィア", "パチンコ", "サイト", "ブラック",
    "プライベート", "フリー", "カンパ", "インチキ", "スポンサー", "テスト",
    "パワハラ", "コミュ", "シャッター", "メガ", "スマホ", "カジノ", "ヤバ",
    "サンキュー",
})


def _katakana_extract_tokens(text: str) -> Iterable[tuple[str, bool]]:
    text = _BRACKET_SPEAKER_PREFIX.sub("", text)
    for m in _KATAKANA_RUN.finditer(text):
        token = m.group()
        if token in STOPWORDS_JA:
            continue
        yield token, False  # no position confound -- every hit self-corroborates


# ---------------------------------------------------------------------------
# Korean (ko): word-spaced, but no capitalization AND no distinct-script
# signal -- a genuinely harder mining problem than tr/ms/ja.
#
# Korean writing (unlike Japanese/Chinese/Thai) DOES separate words with
# real spaces, so word boundaries aren't the problem -- but Hangul has no
# orthographic marker distinguishing a proper noun from a common noun the
# way capitalization or katakana does. Measured 2026-09-29 (5 real
# episodes of "Confidence Queen" (tvdb-444735) + 3 of "Not Others"
# (tvdb-428265), extracted from these files' own embedded Korean subtitle
# streams -- this deployment has no *.ko.srt sidecar files yet): raw
# frequency mining (particle-stripped, 2-4 Hangul-syllable tokens) over
# ONE series' top 40 candidates was only ~5-8% genuine names, the rest
# common pronouns/verb-endings/nouns -- a far worse signal-to-noise ratio
# than Japanese's katakana approach (~50%+ even unfiltered).
#
# What actually discriminates well: a candidate that ALSO recurs (>=3
# occurrences) in a SECOND, unrelated series is real, measured proof it's
# ordinary vocabulary, not a name specific to one show's cast -- a
# genuine character name essentially never appears in an unrelated
# show's dialogue. Checked across those two real, unrelated series: 249
# of ~830 raw candidates recurred in BOTH, and STOPWORDS_KO below is
# exactly that measured overlap set (plus a hand-curated supplement of
# common honorific/kinship/title address terms -- "회장" (chairman),
# "의사" (doctor), "오빠"/"언니"/"형"/"누나" (kinship terms used as direct
# address) -- the same category STOPWORDS_TR's own kinship-term entries
# cover, which the two-series sample was too small to have already
# surfaced empirically). After this filtering, genuine names (제임스/
# James, 전태수, 재희, 조성우, 레이첼/Rachel -- both native Korean names
# and Hangul-transliterated foreign ones) rose to roughly 15-20% of the
# remaining candidates -- a real improvement, but still disclosed as
# meaningfully noisier than tr/ms/ja: expect more non-name suggestions
# (thematic common nouns specific to one show's plot, like "수술"/surgery
# in a hospital-adjacent storyline, have no marker distinguishing them
# from a name and aren't caught by any stopword list). Safe regardless,
# per this module's standing safety framing (hotwords + human-reviewed
# suggestions only).
# ---------------------------------------------------------------------------

_HANGUL_SYLLABLE = re.compile(r"[가-힣]{2,4}")

# Common particles attached directly to the preceding word with no space
# (Korean's agglutinative equivalent of Turkish's apostrophe-suffix
# inflections) -- longest-first so a longer real suffix isn't shadowed by
# a shorter one that's also its own prefix (에게 before 에). Stripped
# before counting so inflected mentions of the same word merge into one
# candidate, same purpose as _SUFFIX_SPLIT for Turkish.
_KOREAN_PARTICLES = ["에게서", "한테서", "이라고", "라고", "에게", "한테", "께서",
                     "으로는", "로는", "으로", "는", "은", "이", "가", "을", "를",
                     "의", "에", "과", "와", "도", "만", "씨", "님", "아", "야",
                     "요", "죠"]
_KOREAN_PARTICLE_RE = re.compile("(" + "|".join(_KOREAN_PARTICLES) + ")$")


def _strip_korean_particle(token: str) -> str:
    m = _KOREAN_PARTICLE_RE.search(token)
    # Guard mirrors Turkish's _SUFFIX_SPLIT intent: never strip a
    # particle down to nothing, or to something below the 2-syllable
    # floor _HANGUL_SYLLABLE requires anyway.
    if m and len(token) > len(m.group()) + 1:
        return token[:-len(m.group())]
    return token


STOPWORDS_KO = frozenset({
    # Empirically measured cross-series overlap (see the comment block
    # above) -- common pronouns, verb/adjective endings, connectives,
    # and everyday nouns, not names.
    "아니", "내가", "엄마", "진짜", "우리", "그럼", "뭐야", "이거", "어떻게",
    "그래", "이게", "지금", "제가", "있어", "아이", "새끼", "그냥", "근데",
    "무슨", "여기", "너무", "아주", "사람", "이렇게", "빨리", "잠깐", "어디",
    "그렇게", "아이고", "하고", "그게", "그거", "오늘", "하나", "그러니까",
    "정말", "한번", "그러면", "이건", "이제", "니가", "없어", "있는", "하는",
    "어머", "많이", "들어", "누가", "같은", "아직", "선생", "같이", "됐어",
    "저기", "그리고", "말이", "가자", "전화", "그래서", "그만", "말고",
    "괜찮", "저희", "생각", "언제", "하지", "이런", "있습니다", "거예",
    "알아", "아냐", "혹시", "그런", "아유", "나는", "다른", "없이", "어떡해",
    "소리", "뭐가", "않아", "가지고", "주세", "일이", "경찰", "저거",
    "같은데", "안녕하세", "바로", "다시", "병원", "했어", "때문", "누구",
    "제발", "좋아", "없는", "나도", "하면", "나와", "해야", "어머니", "친구",
    "아니라", "저는", "아까", "머리", "알았어", "했는데", "그건", "어디서",
    "있는데", "있고", "나쁜", "나가", "나한테", "문제", "뭔데", "말을",
    "괜찮아", "어때", "자기", "얼마나", "얘기", "맞아", "어떤", "그걸",
    "계속", "여기서", "선배", "아이구", "보고", "그치", "그렇지", "몰라",
    "조금", "그때", "내일", "집에", "정신", "인간", "인생", "당신", "알고",
    "하시", "전에", "아니면", "왜요", "가세", "모두", "거기", "있지",
    "드라마", "생각해", "모든", "먹어", "아파", "아저", "없는데", "얼굴",
    "아닌데", "먹고", "자꾸", "미안해", "오빠", "받아", "일단", "기다려",
    "있네", "만들어", "기억", "너의", "너는", "사장", "아닙니다", "제대로",
    "보니까", "요즘", "보자", "남자", "다음", "볼까", "왔어", "미리",
    "혼자", "잡아", "잘못", "있다", "환자", "마음", "해도", "나를", "아우",
    "봤어", "뭔가", "가서", "합니다", "있어서", "조용히", "되지", "없고",
    "알지", "아들", "먼저", "저도", "아니에", "설마", "금방", "가요",
    "왔습니다", "어딜", "마지막", "보이", "없어서", "아는", "뭐냐", "남의",
    "대한", "봐도", "어우", "놈의", "불러", "해서", "선생님", "나오", "하자",
    "계세", "영화", "간다", "될까", "해라", "한다", "가는", "준비해",
    "스톱", "왔다", "변호사", "처음", "얘가", "여보세", "형님", "올게",
    "그랬어", "회사", "싫어", "아동", "주고", "와서",
    # Hand-curated supplement (2026-09-29, not yet surfaced by the small
    # two-series sample above): honorific/kinship/title terms used as a
    # form of direct address, the same category STOPWORDS_TR carries for
    # Turkish (Anne, Baba, Bey, Hanım...).
    "회장", "보스", "의사", "작가", "어르신", "사모", "언니", "형", "누나",
    "아빠", "아버지", "할머니", "할아버지", "이모", "삼촌", "고모", "네가",
})


def _hangul_extract_tokens(text: str) -> Iterable[tuple[str, bool]]:
    for raw in text.split():
        token = raw.strip(_STRIP_CHARS)
        token = _strip_korean_particle(token)
        if _HANGUL_SYLLABLE.fullmatch(token) and token not in STOPWORDS_KO:
            yield token, False  # no position confound -- every hit self-corroborates


# ---------------------------------------------------------------------------
# Chinese (zh): no word spacing AND no capitalization-equivalent marker --
# a genuinely harder problem than ko's (which at least has real word
# boundaries).
#
# Measured 2026-09-29 (4 real episodes of "Pull Strings" (tvdb-467966) +
# 4 of "A Familiar Stranger" (tvdb-425354), extracted from these series'
# own embedded Chinese subtitle streams -- same method as ko, no *.zh.srt
# sidecar files exist yet). Without real word boundaries, candidates are
# character n-grams (2-4 Hanzi, sliding window over each contiguous CJK
# run) rather than whitespace-split tokens -- necessarily noisier before
# filtering, since a real word's own substrings ("先元" and "元剑" inside
# the real 3-character term "先元剑") also independently clear the
# frequency thresholds. The same cross-series-overlap technique that
# worked for ko discriminates well here too: STOPWORDS_ZH below is
# exactly the measured 2-series overlap (common pronouns/grammar
# function words -- "什么", "我们", "知道", "可以", ...). What's left after
# that filtering is dominated by real, thematically-relevant recurring
# terms for this one show (a character name "长庚", a place "熊岛"/Bear
# Island, sect/organization names "西昉教", "度仙门", an artifact name
# "先元剑") -- exactly the class of entity a series glossary exists to
# protect, the スマイル/"Smile" pattern CLAUDE.md documents.
#
# The substring-redundancy artifact is handled separately, in
# _collapse_substring_redundant_zh_candidates() below: when a shorter
# candidate's occurrences are (almost) entirely subsumed by a longer
# candidate that contains it (e.g. "先元" and "元剑" both mostly appear
# as part of "先元剑"), only the longer, more informative candidate is
# kept -- so a human reviewer sees one real term, not three overlapping
# fragments of it.
# ---------------------------------------------------------------------------

_CJK_RUN = re.compile(r"[一-鿿]+")

STOPWORDS_ZH = frozenset({
    "什么", "我们", "怎么", "就是", "知道", "不是", "你们", "他们", "一个",
    "这个", "没有", "来了", "是你", "已经", "一定", "你的", "现在", "我不",
    "你不", "我就", "这么", "自己", "没事", "的人", "那个", "是我", "你是",
    "是不", "不过", "是什", "是什么", "还有", "可以", "一下", "这是", "有什",
    "我的", "有什么", "有人", "我知道", "让我", "你说", "了一", "我知",
    "你就", "过来", "那么", "有一", "应该", "给我", "了吗", "不要", "起来",
    "放心", "我是", "都不", "告诉", "我要", "时候", "了我", "让你", "真的",
    "个人", "不会", "到了", "下来", "所有", "来的", "可是", "若是", "为了",
    "人的", "为何", "原来", "东西", "你怎么", "你放", "哪儿", "么了", "你怎",
    "的是", "过去", "如何", "就好", "么样", "意思", "出去", "怎么了",
    "怎么样", "只是", "去了", "听说", "我都", "我有", "这一", "这里", "喜欢",
    "也是", "那些", "的那", "的话", "你别", "愿意", "是真", "是谁", "说什",
    "给你", "说什么", "说的", "自然", "你还", "他的", "有没", "里的", "进去",
    "今日", "我没", "走吧", "有没有", "然后", "这就", "快去", "去吧", "是假",
    "有一个", "救你", "重要",
})


def _zh_extract_tokens(text: str) -> Iterable[tuple[str, bool]]:
    for run in _CJK_RUN.finditer(text):
        s = run.group()
        for n in (2, 3, 4):
            for i in range(len(s) - n + 1):
                token = s[i:i + n]
                if token not in STOPWORDS_ZH:
                    yield token, False  # no position confound


def _collapse_substring_redundant_zh_candidates(
        candidates: dict[str, "AutoCandidate"]) -> dict[str, "AutoCandidate"]:
    """A shorter n-gram candidate that's (almost) always just a fragment
    of a longer candidate that contains it -- not independent evidence of
    its own -- is dropped in favor of the longer one. `total_count`
    (rather than requiring an exact match) tolerates a shorter candidate
    also coincidentally starting/ending a handful of unrelated words
    elsewhere: 90% of its occurrences being explained by the longer
    containing candidate is treated as "this is really the same term".
    Longest-first, so a 4-character term absorbs both its 2- and
    3-character sub-fragments in one pass."""
    by_length = sorted(candidates.values(), key=lambda c: -len(c.canonical))
    kept: list[AutoCandidate] = []
    dropped: set[str] = set()
    for cand in by_length:
        if any(cand.canonical in longer.canonical and cand.total_count <= longer.total_count * 1.1
              for longer in kept):
            dropped.add(cand.canonical)
            continue
        kept.append(cand)
    return {c.canonical: c for c in candidates.values() if c.canonical not in dropped}


@dataclass(frozen=True)
class LanguageMiner:
    """One language's proper-noun mining strategy.

    `extract_tokens(cue_text)` yields (token, is_cue_initial) for every
    mining candidate in a cue -- `is_cue_initial` marks an occurrence
    that CANNOT alone corroborate the token as a genuine recurring
    proper noun, because it's confounded with ordinary sentence-initial
    capitalization (Latin scripts); a script with no such confound
    (Japanese, Korean, Chinese) reports False unconditionally, so every
    occurrence corroborates immediately. `is_noise_cue(cue_text)` flags a
    whole cue to skip outright (e.g. an all-Title-Case sung lyric line);
    defaults to never skipping. `collapse_candidates(candidates)` is an
    optional final post-processing pass over the already-qualified
    per-token totals (see Chinese's substring-redundancy collapse);
    defaults to a no-op identity function."""
    extract_tokens: Callable[[str], Iterable[tuple[str, bool]]]
    is_noise_cue: Callable[[str], bool] = staticmethod(lambda text: False)
    collapse_candidates: Callable[[dict], dict] = staticmethod(lambda candidates: candidates)


MINERS: dict[str, LanguageMiner] = {
    "tr": LanguageMiner(
        extract_tokens=_latin_extract_tokens(_LATIN_PROPER_NOUN_TR, STOPWORDS_TR, strip_suffix=True),
        is_noise_cue=_is_latin_title_case_cue),
    "ms": LanguageMiner(
        extract_tokens=_latin_extract_tokens(_LATIN_PROPER_NOUN_MS, STOPWORDS_MS, strip_suffix=False),
        is_noise_cue=_is_latin_title_case_cue),
    "ja": LanguageMiner(extract_tokens=_katakana_extract_tokens),
    "ko": LanguageMiner(extract_tokens=_hangul_extract_tokens),
    "zh": LanguageMiner(extract_tokens=_zh_extract_tokens,
                        collapse_candidates=_collapse_substring_redundant_zh_candidates),
}

# A single episode's ASR mishear can recur a few times within THAT
# episode (a decoder fixation on one bad segment) without being a real
# name -- 3 within one ~40-45 min episode is enough separation from that
# noise floor to count as "used repeatedly" rather than "glitched".
# Reused unchanged across every registered language: validated
# specifically for Japanese too (2026-09-29) against the same real
# transcripts STOPWORDS_JA was measured from -- both confirmed real
# names (スマイル, カズ) clear it from a single episode's occurrences,
# with no evidence yet that a different value is needed per language.
MIN_OCCURRENCES_PER_EPISODE = 3

# Deliberately 1, not higher (changed 2026-09-19, real request after a
# real miss: "Evren" -- a genuine recurring character whose name is also
# an ordinary Turkish word -- went unprotected long enough to visibly
# corrupt translation ("Mr. Universe") before a human noticed). Waiting
# for several episodes to "corroborate" a name trades real, current
# translation quality for a precision margin that promote() (see
# api.py's POST .../glossary/promote) now covers instead: a mined name
# is never auto-applied to translation on its own -- surfacing sooner
# and requiring an explicit one-click human promotion is the actual
# safety boundary now, not an episode count.
MIN_DISTINCT_EPISODES = 1

# Caps the hotwords string from growing unbounded across many seasons of
# a long-running series; ranked by total_count descending before the cap
# is applied, so the most confidently recurring names always win a slot.
TOP_N_CANDIDATES = 30


@dataclass
class AutoCandidate:
    canonical: str
    episode_counts: dict[str, int] = field(default_factory=dict)
    total_count: int = 0


def mine_series_entities(series_root: str | Path, source_lang: str = SOURCE_LANG, *,
                         exclude_srt_path: str | Path | None = None,
                         exclude_canonicals: set[str] | None = None) -> list[AutoCandidate]:
    """Scans every `*.{source_lang}.srt` under series_root (recursive --
    seasons are subdirectories) that has a matching `*.en.srt` sibling
    (paired existence is the "this episode's pipeline run actually
    completed" signal -- an episode mid-processing or that failed before
    writing translation output is not a trustworthy mining source), and
    returns names that recur often enough, within and across episodes,
    to be trusted (see MIN_OCCURRENCES_PER_EPISODE/MIN_DISTINCT_EPISODES).

    Degrades to [] for any `source_lang` not registered in MINERS (no
    orthographic mining strategy exists for it yet -- see this module's
    docstring), same as its existing "no match of any kind" behavior; a
    caller doesn't need to check membership itself first.

    exclude_srt_path (this job's own current/prior source output -- so a
    job never mines its own not-yet-validated, or on retry previous-bad,
    output) and exclude_canonicals (casefolded; already-manually-
    protected names) are both applied DURING counting, not after
    ranking, so an excluded name never occupies one of the
    TOP_N_CANDIDATES slots a genuinely new name could use.

    A per-file read/parse error skips just that one file -- one corrupt
    sibling must not blank out signal from every other good episode. No
    match of any kind returns []."""
    miner = MINERS.get(source_lang)
    if miner is None:
        return []
    root = Path(series_root)
    exclude_path = Path(exclude_srt_path).resolve() if exclude_srt_path else None
    exclude_canonicals = exclude_canonicals or set()

    candidates: dict[str, AutoCandidate] = {}
    # A function word capitalized only because it opened a cue/sentence
    # never earns a slot here -- see LanguageMiner's docstring. Global
    # across the whole series (not per-episode): one genuine mid-cue
    # sighting anywhere is enough to corroborate the name everywhere.
    corroborated: set[str] = set()
    for tr_path in sorted(root.rglob(f"*.{source_lang}.srt")):
        if exclude_path is not None and tr_path.resolve() == exclude_path:
            continue
        en_path = tr_path.with_name(tr_path.name[: -len(f".{source_lang}.srt")] + ".en.srt")
        if not en_path.exists():
            continue
        try:
            cues = srt.parse(tr_path)
        except (OSError, ValueError, UnicodeDecodeError):
            continue

        episode_key = str(tr_path)
        per_episode: dict[str, int] = {}
        for cue in cues:
            if miner.is_noise_cue(cue.text):
                continue
            for token, is_initial in miner.extract_tokens(cue.text):
                if token.casefold() in exclude_canonicals:
                    continue
                per_episode[token] = per_episode.get(token, 0) + 1
                if not is_initial:
                    corroborated.add(token)

        for token, count in per_episode.items():
            if count < MIN_OCCURRENCES_PER_EPISODE:
                continue
            entry = candidates.setdefault(token, AutoCandidate(canonical=token))
            entry.episode_counts[episode_key] = count
            entry.total_count += count

    candidates = miner.collapse_candidates(candidates)
    qualified = [c for c in candidates.values()
                if len(c.episode_counts) >= MIN_DISTINCT_EPISODES and c.canonical in corroborated]
    qualified.sort(key=lambda c: c.total_count, reverse=True)
    return qualified[:TOP_N_CANDIDATES]


def detect_series_mining_language(series_root: str | Path) -> str | None:
    """Which registered MINERS language this series' own already-
    completed episodes are actually in, found by looking at what
    `*.{lang}.srt` siblings exist under series_root -- not guessed from
    the current job (see mine_series_entities_auto()'s docstring for why
    that's the wrong question to ask here). Returns None if no
    registered language has any matching file (nothing mined yet, or
    the series is in a language MINERS doesn't cover)."""
    root = Path(series_root)
    for lang in MINERS:
        if next(root.rglob(f"*.{lang}.srt"), None) is not None:
            return lang
    return None


def mine_series_entities_auto(series_root: str | Path, *,
                              exclude_srt_path: str | Path | None = None,
                              exclude_canonicals: set[str] | None = None) -> list[AutoCandidate]:
    """mine_series_entities(), but for a caller that doesn't know (or
    shouldn't assume) the series' source language up front -- real gap
    fixed 2026-09-29: worker.py's own suggestion-refresh used to call
    mine_series_entities() with the hardcoded module-level SOURCE_LANG
    ("tr") for every job regardless of the series' actual language, so
    non-Turkish series always mined zero candidates silently. Mining
    targets *other, already-resolved* episodes of the same series (see
    this module's docstring) -- their language is a property of the
    series, discoverable from what's already on disk
    (detect_series_mining_language()), not of the job invoking this,
    which may not know its own resolved language yet (AUTO source-
    language mode isn't resolved until ASR runs)."""
    lang = detect_series_mining_language(series_root)
    if lang is None:
        return []
    return mine_series_entities(series_root, lang, exclude_srt_path=exclude_srt_path,
                                exclude_canonicals=exclude_canonicals)


def write_suggestions(suggestions_dir: str | Path, tvdb_id: int,
                      candidates: list[AutoCandidate], *, title: str | None = None) -> Path:
    """Writes <suggestions_dir>/<tvdb_id>.yaml, schema-identical to a
    real /glossary series file (tvdb_id/title/entities[canonical,
    aliases, protected]) plus reviewer-only extra keys (occurrences,
    distinct_episodes) -- load_profile() already ignores every key on a
    non-`protected: true` entity, so this file is safe even if ever
    pointed at by mistake as a real glossary source. `protected: false`
    by default: promoting a name to translation protection is a human
    decision, made by copying it into the real /glossary YAML.

    Full regenerate-and-overwrite every call -- this module recomputes
    per job, statelessly; see module docstring."""
    directory = Path(suggestions_dir)
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "tvdb_id": tvdb_id,
        "title": title,
        "entities": [
            {
                "canonical": c.canonical,
                "aliases": [],
                "protected": False,
                "occurrences": c.total_count,
                "distinct_episodes": len(c.episode_counts),
            }
            for c in candidates
        ],
    }
    target = directory / f"{tvdb_id}.yaml"
    glossary_files.write_text_atomic(
        target, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return target
