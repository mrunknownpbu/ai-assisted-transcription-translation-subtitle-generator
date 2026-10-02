# Entity-protection precedent: the evidence bar

*Standing policy, moved verbatim from `CLAUDE.md` (2026-10-02).*


Before adding a name to a series glossary's `protected: true` list
(`glossary/<series>.yaml`), the established bar from real investigations
this project has done is: (1) confirm it's a genuinely recurring
character via corpus-wide occurrence counts across many episodes, not
just the one flagged instance, AND (2) find at least one concrete
example of it being mistranslated/hallucinated -- a common-word
collision ("Cenk" = "war", "Evren" = "universe", "Erdem" = "virtue",
"Deniz" = "sea"), a bare-exclaimed-name hallucination ("Sirius!" ->
"Sirius, what are you doing?"), or inconsistent transliteration. A name
that's merely recurring but shows no confirmed bug (e.g. "Ayfer" in the
Love Is In The Air investigation) is deliberately left unprotected --
protection isn't free of risk for common-word collisions, so don't add
it speculatively. A one-scene character (e.g. "Fatma", "Faruk") is
excluded regardless of translation quality, on recurrence alone.

`auto_glossary.py` mines a series' own already-completed episodes for
candidate names automatically, but only as suggestions for review, never
translation protection directly -- promoting a MINED name to `protected:
true` is always a deliberate human/session decision. The one automatic
path is `cast_enrichment.py` (see `docs/decisions/2026-09-28-cast-metadata-tvdb-tmdb-imdb-feed-name-protection.md`), which enforces this same bar
in code -- credited by cast metadata, recurring as a name, and a measured
mistranslation -- and scopes what it protects to credited episodes.
