"""Resolves a user-specified target language (any language, on demand — no fixed list)
into the FLORES-200 tag NLLB needs (e.g. "spa_Latn"). Two paths:

1. A convenience table covering commonly requested languages by ISO 639-1 code or common
   English name ("es" / "spanish" -> "spa_Latn").
2. A direct-passthrough escape hatch: if the user already supplies a valid-looking
   FLORES-200 tag (`xxx_Xxxx`), it's used as-is — this is what keeps the system honestly
   "any of NLLB's ~200 languages on demand" rather than limited to the convenience table's
   coverage, without requiring us to hand-maintain all 200 entries.

Unsupported/unrecognized input raises `UnsupportedLanguageError` with a clear message
rather than silently substituting a default language.
"""
from __future__ import annotations

import re

_FLORES_TAG_RE = re.compile(r"^[a-z]{3}_[A-Z][a-z]{3}$")

# ISO 639-1 code (and a few common full names) -> FLORES-200 tag.
# Not exhaustive of NLLB's ~200 languages by design — see module docstring for the
# direct-FLORES-tag escape hatch covering anything not listed here.
_COMMON_LANGUAGE_MAP: dict[str, str] = {
    "en": "eng_Latn", "english": "eng_Latn",
    "es": "spa_Latn", "spanish": "spa_Latn",
    "fr": "fra_Latn", "french": "fra_Latn",
    "de": "deu_Latn", "german": "deu_Latn",
    "it": "ita_Latn", "italian": "ita_Latn",
    "pt": "por_Latn", "portuguese": "por_Latn",
    "nl": "nld_Latn", "dutch": "nld_Latn",
    "ru": "rus_Cyrl", "russian": "rus_Cyrl",
    "uk": "ukr_Cyrl", "ukrainian": "ukr_Cyrl",
    "pl": "pol_Latn", "polish": "pol_Latn",
    "tr": "tur_Latn", "turkish": "tur_Latn",
    "ar": "arb_Arab", "arabic": "arb_Arab",
    "he": "heb_Hebr", "hebrew": "heb_Hebr",
    "fa": "pes_Arab", "persian": "pes_Arab", "farsi": "pes_Arab",
    "hi": "hin_Deva", "hindi": "hin_Deva",
    "bn": "ben_Beng", "bengali": "ben_Beng",
    "ur": "urd_Arab", "urdu": "urd_Arab",
    "ta": "tam_Taml", "tamil": "tam_Taml",
    "te": "tel_Telu", "telugu": "tel_Telu",
    "ja": "jpn_Jpan", "japanese": "jpn_Jpan",
    "ko": "kor_Hang", "korean": "kor_Hang",
    "zh": "zho_Hans", "chinese": "zho_Hans", "zh-cn": "zho_Hans",
    "zh-tw": "zho_Hant", "zh-hant": "zho_Hant",
    "vi": "vie_Latn", "vietnamese": "vie_Latn",
    "th": "tha_Thai", "thai": "tha_Thai",
    "id": "ind_Latn", "indonesian": "ind_Latn",
    "ms": "zsm_Latn", "malay": "zsm_Latn",
    "tl": "tgl_Latn", "tagalog": "tgl_Latn", "filipino": "tgl_Latn",
    "sw": "swh_Latn", "swahili": "swh_Latn",
    "am": "amh_Ethi", "amharic": "amh_Ethi",
    "ha": "hau_Latn", "hausa": "hau_Latn",
    "yo": "yor_Latn", "yoruba": "yor_Latn",
    "ig": "ibo_Latn", "igbo": "ibo_Latn",
    "zu": "zul_Latn", "zulu": "zul_Latn",
    "af": "afr_Latn", "afrikaans": "afr_Latn",
    "el": "ell_Grek", "greek": "ell_Grek",
    "hu": "hun_Latn", "hungarian": "hun_Latn",
    "cs": "ces_Latn", "czech": "ces_Latn",
    "sk": "slk_Latn", "slovak": "slk_Latn",
    "ro": "ron_Latn", "romanian": "ron_Latn",
    "bg": "bul_Cyrl", "bulgarian": "bul_Cyrl",
    "sr": "srp_Cyrl", "serbian": "srp_Cyrl",
    "hr": "hrv_Latn", "croatian": "hrv_Latn",
    "sl": "slv_Latn", "slovenian": "slv_Latn",
    "sv": "swe_Latn", "swedish": "swe_Latn",
    "no": "nob_Latn", "norwegian": "nob_Latn",
    "da": "dan_Latn", "danish": "dan_Latn",
    "fi": "fin_Latn", "finnish": "fin_Latn",
    "et": "est_Latn", "estonian": "est_Latn",
    "lv": "lvs_Latn", "latvian": "lvs_Latn",
    "lt": "lit_Latn", "lithuanian": "lit_Latn",
    "ka": "kat_Geor", "georgian": "kat_Geor",
    "hy": "hye_Armn", "armenian": "hye_Armn",
    "az": "azj_Latn", "azerbaijani": "azj_Latn",
    "kk": "kaz_Cyrl", "kazakh": "kaz_Cyrl",
    "uz": "uzn_Latn", "uzbek": "uzn_Latn",
    "mn": "khk_Cyrl", "mongolian": "khk_Cyrl",
    "ne": "npi_Deva", "nepali": "npi_Deva",
    "si": "sin_Sinh", "sinhala": "sin_Sinh",
    "my": "mya_Mymr", "burmese": "mya_Mymr",
    "km": "khm_Khmr", "khmer": "khm_Khmr",
    "lo": "lao_Laoo", "lao": "lao_Laoo",
    "is": "isl_Latn", "icelandic": "isl_Latn",
    "ga": "gle_Latn", "irish": "gle_Latn",
    "cy": "cym_Latn", "welsh": "cym_Latn",
    "eu": "eus_Latn", "basque": "eus_Latn",
    "ca": "cat_Latn", "catalan": "cat_Latn",
    "gl": "glg_Latn", "galician": "glg_Latn",
    "mt": "mlt_Latn", "maltese": "mlt_Latn",
}


class UnsupportedLanguageError(ValueError):
    pass


def resolve_flores_code(user_input: str) -> str:
    if not user_input:
        raise UnsupportedLanguageError("No target language specified")

    normalized = user_input.strip()
    if _FLORES_TAG_RE.match(normalized):
        return normalized  # direct FLORES-200 tag passthrough

    key = normalized.lower()
    if key in _COMMON_LANGUAGE_MAP:
        return _COMMON_LANGUAGE_MAP[key]

    raise UnsupportedLanguageError(
        f"Could not resolve target language '{user_input}' to a FLORES-200 code. "
        "Use an ISO 639-1 code, a common English language name, or a direct FLORES-200 "
        "tag (e.g. 'ban_Latn') for languages not in the convenience list."
    )
