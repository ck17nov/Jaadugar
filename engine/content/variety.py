"""Is this bank varied enough to publish from?

THE FAILURE THIS EXISTS TO CATCH. Three hundred stories generated in one
session by one model, all satisfying a gate that demands a named protagonist,
three failed attempts, a verbatim refrain and child-solves-it agency, will be
three hundred tellings of one story. YouTube's policy prohibits content
"giving the impression of mass production" and names putting characters "in
the same situation over and over again with the same outcome". The existing
story gate makes that MORE likely, not less - it enforces sameness of shape by
design, which is right for craft and wrong for a catalogue.

WHY WORD OVERLAP IS NOT ENOUGH, measured. `originality.py` compares scripts
with 4-gram word Jaccard against a 0.80 threshold. A rename-only duplicate -
the identical story with the character called Ravi instead of Milo - scores
0.385. It sails through. So this module compares two other things: the story
DESCRIBED BY ITS AXES (a six-field tuple), and the wording with the declared
character names MASKED OUT.

Masking is exact rather than statistical because the entry DECLARES its
characters. No entity recogniser is needed, which is the reason the schema
asks for a character bible even for content that does not need one visually.

numpy and stdlib only. No embeddings, no torch, no scikit-learn - and it must
stay that way, because a gate that needs a 2 GB wheel on a 2-core ARM box is a
gate that gets skipped.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from ..core.util import words as split_words

# MinHash width. 128 gives a standard error of about 0.035 at J=0.2, which is
# comfortably tighter than the gap between the screen (0.12) and the reject
# line (0.55) - so the cheap stage never has to be trusted for the verdict,
# only for deciding what to compare exactly.
SIGNATURE_WIDTH = 128
_PRIME = (1 << 31) - 1

# Screen wide, judge narrow. Anything estimated above SCREEN gets an exact
# comparison; only the exact score can reject.
SCREEN_SIMILARITY = 0.12
REJECT_SIMILARITY = 0.55
WARN_SIMILARITY = 0.45

# Five of six axes identical means one substitution away from the same story.
MIN_AXIS_DIFFERENCES = 2

# No single arc may own more than this share of a group's bank.
MAX_ARC_SHARE = 0.20
MAX_OUTCOME_SHARE = 0.30
# A character name may recur - a series is fine - but not everywhere.
MAX_NAME_SHARE = 0.15

_SEED = 20260910          # PINNED: signatures must compare across imports


def _perms(width: int = SIGNATURE_WIDTH) -> tuple[np.ndarray, np.ndarray]:
    """Hash permutations. Pinned seed, or two imports cannot be compared."""
    rng = np.random.default_rng(_SEED)
    a = rng.integers(1, _PRIME, size=width, dtype=np.int64)
    b = rng.integers(0, _PRIME, size=width, dtype=np.int64)
    return a, b


_A, _B = _perms()


# What a masked name is replaced BY.
#
# Alphabetic, because `util.words()` strips punctuation - the old placeholder
# was " @ " and "@" is in util._PUNCT, so every masked name vanished entirely
# instead of becoming a token. Two stories that differ only in name then had
# DIFFERENT token counts at every name position, and the 4-gram windows either
# side of each name were destroyed rather than aligned.
_PLACEHOLDER = " xnamex "


def mask_names(text: str, names: Iterable[str]) -> str:
    """Replace declared character names with a placeholder.

    This is what turns "the same story with a different protagonist" from
    invisible into obvious. Longest first, so "Mia Rose" is masked before
    "Mia" leaves a dangling "Rose".

    WORD-BOUNDED, and that is not a nicety. Without \\b the substitution
    matched inside words, so masking destroyed the overlap it exists to
    measure: with a short name the wreckage was severe enough that a
    byte-identical story escaped the gate completely. Measured, on the real
    code, with protagonists "Milo" and "Al" over a story using wall / tall /
    small / ball / called - raw 4-gram Jaccard 0.563, which is ABOVE the 0.55
    reject line, but masked 0.055 and a MinHash estimate of 0.062, below the
    0.12 screen. So `exact_similarity` was never called, "only the exact
    score may reject" never engaged, and `check_new` returned nothing. Both
    entries imported clean.

    A name with no letters at either end - punctuation, or a name that is
    itself a substring the author intended - still cannot be bounded, so
    `\\b` is applied only where it means something.
    """
    out = text or ""
    for name in sorted({n.strip() for n in names if n and n.strip()},
                       key=len, reverse=True):
        escaped = re.escape(name)
        # \b only asserts a boundary next to a word character. Anchoring
        # against a name that starts or ends with punctuation would make the
        # pattern unmatchable rather than safer.
        left = r"\b" if name[:1].isalnum() else ""
        right = r"\b" if name[-1:].isalnum() else ""
        out = re.sub(f"{left}{escaped}{right}", _PLACEHOLDER, out,
                     flags=re.IGNORECASE)
    return out


def _grams(text: str, size: int = 4) -> set[str]:
    tokens = split_words(text)
    if len(tokens) < size:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + size])
            for i in range(len(tokens) - size + 1)}


def signature(text: str, width: int = SIGNATURE_WIDTH) -> np.ndarray:
    """MinHash signature of a text's 4-grams.

    An all-max signature means "no content", and `estimate` treats a pair
    involving one as dissimilar rather than identical - two empty texts are
    not a duplicate, they are two mistakes.
    """
    grams = _grams(text)
    if not grams:
        return np.full(width, np.iinfo(np.int64).max, dtype=np.int64)
    hashed = np.array(
        [int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=8)
                        .digest(), "big") % _PRIME for g in grams],
        dtype=np.int64)
    # (len(grams), width) then min down the gram axis.
    spread = (_A[None, :] * hashed[:, None] + _B[None, :]) % _PRIME
    return spread.min(axis=0)


def estimate(left: np.ndarray, right: np.ndarray) -> float:
    """Estimated Jaccard from two signatures."""
    sentinel = np.iinfo(np.int64).max
    if (left == sentinel).all() or (right == sentinel).all():
        return 0.0
    return float((left == right).mean())


def exact_similarity(left: str, right: str) -> float:
    """True 4-gram Jaccard. Only this may reject."""
    a, b = _grams(left), _grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
@dataclass
class VarietyIssue:
    entry_id: str
    kind: str
    message: str
    fatal: bool = True
    against: str = ""

    def __str__(self) -> str:
        mark = "REJECT" if self.fatal else "warn  "
        tail = f" (vs {self.against})" if self.against else ""
        return f"{mark} {self.entry_id} [{self.kind}] {self.message}{tail}"


@dataclass
class BankStats:
    """What the bank looks like as a whole, for the report."""
    total: int = 0
    arcs: Counter = field(default_factory=Counter)
    outcomes: Counter = field(default_factory=Counter)
    names: Counter = field(default_factory=Counter)
    scene_counts: Counter = field(default_factory=Counter)
    seconds: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "arcs": dict(self.arcs.most_common()),
            "outcomes": dict(self.outcomes.most_common()),
            "top_names": dict(self.names.most_common(10)),
            "scene_counts": dict(sorted(self.scene_counts.items())),
            "seconds_min": round(min(self.seconds), 1) if self.seconds else 0,
            "seconds_max": round(max(self.seconds), 1) if self.seconds else 0,
        }


def _masked_text(entry) -> str:
    names = [c.get("name", "") for c in (entry.characters or [])]
    return mask_names(" ".join(entry.narrations()), names)


def describe(entries: Sequence[Any]) -> BankStats:
    """Summarise a bank. Used for the report and for the share caps."""
    stats = BankStats(total=len(entries))
    for entry in entries:
        if entry.arc_variant:
            stats.arcs[entry.arc_variant] += 1
        if entry.outcome_class:
            stats.outcomes[entry.outcome_class] += 1
        for character in (entry.characters or []):
            name = (character.get("name") or "").strip().lower()
            if name:
                stats.names[name] += 1
        stats.scene_counts[entry.scene_count] += 1
        stats.seconds.append(entry.estimated_seconds)
    return stats


def check_new(candidate: Any, existing: Sequence[Any]) -> list[VarietyIssue]:
    """Is this entry different enough from what is already banked?

    Compared only against entries in the SAME group and language: an English
    finance explainer resembling a Hindi bedtime story is not a finding.
    """
    issues: list[VarietyIssue] = []
    peers = [e for e in existing
             if e.group == candidate.group and e.language == candidate.language
             and e.entry_id != candidate.entry_id]

    # ---- identical narration ------------------------------------------
    for peer in peers:
        if peer.content_hash and peer.content_hash == candidate.content_hash:
            issues.append(VarietyIssue(
                candidate.entry_id, "duplicate",
                "byte-identical narration", True, peer.entry_id))
            return issues

    # ---- the refrain is the entry's signature -------------------------
    if candidate.refrain:
        needle = candidate.refrain.strip().lower()
        for peer in peers:
            if (peer.refrain or "").strip().lower() == needle:
                issues.append(VarietyIssue(
                    candidate.entry_id, "refrain",
                    f"refrain {candidate.refrain!r} is already used", True,
                    peer.entry_id))

    # ---- the six axes -------------------------------------------------
    if candidate.arc_variant:            # arc shapes only
        mine = candidate.diversity_tuple()
        for peer in peers:
            theirs = peer.diversity_tuple()
            differences = sum(1 for x, y in zip(mine, theirs) if x != y)
            if differences < MIN_AXIS_DIFFERENCES:
                issues.append(VarietyIssue(
                    candidate.entry_id, "axes",
                    f"only {differences} of 6 diversity axes differ - this is "
                    f"the same story with a substitution", True, peer.entry_id))
                break

    # ---- wording, with names masked -----------------------------------
    #
    # Two stages because exact pairwise comparison over a whole bank is
    # quadratic in set intersections. MinHash screens; only the exact score
    # is allowed to reject.
    mine_text = _masked_text(candidate)
    mine_sig = signature(mine_text)
    for peer in peers:
        peer_text = _masked_text(peer)
        if estimate(mine_sig, signature(peer_text)) < SCREEN_SIMILARITY:
            continue
        score = exact_similarity(mine_text, peer_text)
        if score >= REJECT_SIMILARITY:
            issues.append(VarietyIssue(
                candidate.entry_id, "wording",
                f"{score:.0%} of its 4-grams match, with character names "
                f"masked out", True, peer.entry_id))
            break
        if score >= WARN_SIMILARITY:
            issues.append(VarietyIssue(
                candidate.entry_id, "wording",
                f"{score:.0%} 4-gram overlap with names masked", False,
                peer.entry_id))

    # ---- share caps, judged against the bank this entry would join ----
    stats = describe(list(peers) + [candidate])
    if candidate.arc_variant and stats.total >= 10:
        share = stats.arcs[candidate.arc_variant] / stats.total
        if share > MAX_ARC_SHARE:
            issues.append(VarietyIssue(
                candidate.entry_id, "arc_share",
                f"arc {candidate.arc_variant!r} would be {share:.0%} of this "
                f"group; cap is {MAX_ARC_SHARE:.0%}", False))
        outcome_share = stats.outcomes[candidate.outcome_class] / stats.total
        if outcome_share > MAX_OUTCOME_SHARE:
            issues.append(VarietyIssue(
                candidate.entry_id, "outcome_share",
                f"outcome {candidate.outcome_class!r} would be "
                f"{outcome_share:.0%}; cap is {MAX_OUTCOME_SHARE:.0%}", False))
        for character in (candidate.characters or []):
            name = (character.get("name") or "").strip().lower()
            if not name:
                continue
            name_share = stats.names[name] / stats.total
            if name_share > MAX_NAME_SHARE:
                issues.append(VarietyIssue(
                    candidate.entry_id, "name_share",
                    f"the name {name!r} would be in {name_share:.0%} of this "
                    f"group; cap is {MAX_NAME_SHARE:.0%}", False))

    return issues
