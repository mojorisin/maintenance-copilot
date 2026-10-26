"""Keyword search over the equipment manuals, with stable citation IDs.

Each manual is split at its `##` headings; every section becomes one
searchable chunk with an ID like `manual:gas-turbine-ad30#high-vibration-alarm`.
Chunks are ranked with BM25, the classic search-engine scoring formula:

  score = sum over query terms of
          IDF(term) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len / avg_len))

IDF rewards rare terms, tf rewards repeated terms with diminishing returns,
and the length term stops long sections winning just by being long.

Why keyword search rather than embeddings: three short manuals, and queries
full of exact technical terms (T30, Ps30, "seal flush"). BM25 needs no model
download or API call, is deterministic, and every ranking can be explained.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from maintenance_copilot.config import MANUALS_DIR

K1 = 1.5
B = 0.75
# fmt: off
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from", "has",
    "have", "how", "i", "in", "is", "it", "its", "of", "on", "or", "our", "say", "says",
    "should", "that", "the", "this", "to", "was", "we", "what", "when", "which", "with",
})
# fmt: on


def tokenize(text: str) -> list[str]:
    """Lowercase words and numbers, drop stopwords, strip a plural 's'."""
    tokens = []
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if tok in STOPWORDS:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        tokens.append(tok)
    return tokens


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass(frozen=True)
class Section:
    """One searchable chunk: a `##` section of a manual."""

    id: str
    manual_title: str
    asset_type: str
    heading: str
    text: str


@dataclass(frozen=True)
class Hit:
    section: Section
    score: float


def parse_manual(path: Path) -> list[Section]:
    """Split a manual into sections using its front matter and `##` headings."""
    raw = path.read_text()
    _, front, body = raw.split("---", 2)
    meta = dict(line.split(":", 1) for line in front.strip().splitlines())
    meta = {k.strip(): v.strip() for k, v in meta.items()}

    sections = []
    for block in re.split(r"^## ", body, flags=re.MULTILINE)[1:]:
        heading, _, text = block.partition("\n")
        sections.append(
            Section(
                id=f"manual:{meta['id']}#{slugify(heading)}",
                manual_title=meta["title"],
                asset_type=meta["asset_type"],
                heading=heading.strip(),
                text=text.strip(),
            )
        )
    return sections


class ManualIndex:
    """An in-memory BM25 index over every manual section."""

    def __init__(self, sections: list[Section]):
        self.sections = sections
        # The heading is counted twice: a query matching the section title is
        # a stronger signal than the same word somewhere in the body.
        self._tf = [
            Counter(tokenize(f"{s.heading} {s.heading} {s.manual_title} {s.text}"))
            for s in sections
        ]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg_len = sum(self._len) / len(self._len)
        df = Counter(term for tf in self._tf for term in tf)
        n = len(sections)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    @classmethod
    def load(cls, manuals_dir: Path = MANUALS_DIR) -> "ManualIndex":
        sections = [s for p in sorted(manuals_dir.glob("*.md")) for s in parse_manual(p)]
        return cls(sections)

    def _score(self, i: int, terms: list[str]) -> float:
        tf, length = self._tf[i], self._len[i]
        score = 0.0
        for t in terms:
            if t in tf:
                norm = K1 * (1 - B + B * length / self._avg_len)
                score += self._idf[t] * tf[t] * (K1 + 1) / (tf[t] + norm)
        return score

    def search(self, query: str, asset_type: str | None = None, k: int = 3) -> list[Hit]:
        """Top-k sections for `query`, optionally limited to one asset type.

        Returns an empty list when no section shares a term with the query,
        so callers can say "the manuals don't cover this" instead of guessing.
        """
        terms = tokenize(query)
        hits = [
            Hit(s, round(self._score(i, terms), 3))
            for i, s in enumerate(self.sections)
            if asset_type is None or s.asset_type == asset_type
        ]
        hits = [h for h in hits if h.score > 0]
        return sorted(hits, key=lambda h: (-h.score, h.section.id))[:k]

    def get(self, section_id: str) -> Section | None:
        """Look up a section by its citation ID."""
        return next((s for s in self.sections if s.id == section_id), None)
