"""Text case conversion, word replacement and the keep-case exception list.

Mirrors Tag&Rename's four case modes plus its "File Rename Strings" options
(Word replacements, Always keep case of these words). Per the help file the
replacement table is *not* case sensitive, and the keep-case list only has an
effect in the two Capitalize modes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Case(Enum):
    NONE = "none"
    LOWER = "lower"
    UPPER = "upper"
    TITLE = "title"       # Capitalize First Letter (of each word)
    SENTENCE = "sentence"  # Capitalize first word only


# A "word" for capitalisation purposes. Keeping apostrophes inside the word
# stops "Don't" turning into "Don'T".
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


@dataclass
class CaseRules:
    """Settings shared by the rename, filename-to-tag and tag editor tabs."""

    mode: Case = Case.NONE
    #: lowercase search term -> exact replacement, applied before casing
    replacements: dict[str, str] = field(default_factory=dict)
    #: words whose spelling must survive the Capitalize modes verbatim
    keep_case: tuple[str, ...] = ()

    def _keep_map(self) -> dict[str, str]:
        return {w.lower(): w for w in self.keep_case}

    def apply(self, text: str) -> str:
        if not text:
            return text
        text = self._replace_words(text)
        if self.mode is Case.NONE:
            return text
        if self.mode is Case.LOWER:
            return text.lower()
        if self.mode is Case.UPPER:
            return text.upper()
        return self._capitalize(text)

    def _replace_words(self, text: str) -> str:
        if not self.replacements:
            return text
        # Longest first so "feat." wins over "feat".
        for src in sorted(self.replacements, key=len, reverse=True):
            text = re.sub(re.escape(src), self.replacements[src],
                          text, flags=re.IGNORECASE)
        return text

    def _capitalize(self, text: str) -> str:
        keep = self._keep_map()
        first = [True]

        def fix(m: re.Match[str]) -> str:
            word = m.group(0)
            if word.lower() in keep:
                first[0] = False
                return keep[word.lower()]
            if self.mode is Case.SENTENCE and not first[0]:
                return word.lower()
            first[0] = False
            return word[0].upper() + word[1:].lower()

        return _WORD.sub(fix, text)
