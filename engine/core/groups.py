"""Channel groups: one brand channel's worth of topics, under one name.

Why this exists. A YouTube token is bound to ONE channel, so publishing to
several brand channels means one authorisation each, and something has to
decide which video goes where. That decision was made by mapping individual
TOPICS to channels - and there are twenty-odd topics, nine of them beginning
with "kids", so setting a kids channel up meant ticking nine boxes and
remembering not to miss one. Asked for directly: "instead what I wanted is on
the create tab on top above topics, I wanted to create a niche kind of group
meaning like based on our channel name, which is like financial jaadugar, or
kids jaadugar, or technical jaadugar, so based on that there should be a group,
which should be linked to those brand accounts directly. And then below that I
can select topics".

So a group is the unit of channel mapping, and a topic is the unit of content.
Six groups instead of twenty-two topics: one tick per channel.

This module is the SINGLE SOURCE OF TRUTH. The Android app renders the same
list, fetched from /niche-groups rather than hard-coded, because two hand-kept
copies of a list like this drift and the drift is silent - a topic missing from
the app's copy simply cannot be selected, and a topic missing from this copy
maps to no channel at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Group:
    """One brand channel's subject area."""
    key: str
    label: str
    # The channel name this group is expected to publish to. Cosmetic - the
    # real link is the mapping the user makes in Settings - but it is what
    # makes the dropdown recognisable rather than abstract.
    suggested_channel: str
    topics: tuple[str, ...] = field(default_factory=tuple)
    # Everything in this group is child-directed, whatever the topic. Kids is
    # the only one, and it is not a preference: it selects a stricter safety
    # profile and forces the Made-for-Kids classification.
    child_directed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label,
                "suggested_channel": self.suggested_channel,
                "topics": list(self.topics),
                "child_directed": self.child_directed}


# Order is display order. Kids first because it is the largest and the one
# whose topic list made the old per-topic mapping painful.
GROUPS: tuple[Group, ...] = (
    Group(
        key="kids",
        label="Kids",
        suggested_channel="Kids Jaadugar",
        child_directed=True,
        topics=(
            "kids bedtime stories",
            "kids moral stories",
            "kids rhymes and poems",
            "kids alphabet learning",
            "kids numbers and counting",
            "kids words and spelling",
            "kids sentences and speaking",
            "kids toys and play",
            "kids shapes and colours",
        ),
    ),
    Group(
        key="finance",
        label="Finance",
        suggested_channel="Financial Jaadugar",
        topics=("personal finance", "finance news"),
    ),
    Group(
        key="tech",
        label="Tech",
        suggested_channel="Technical Jaadugar",
        topics=("youtube tips and growth", "pc and laptop tech"),
    ),
    Group(
        key="ai",
        label="AI",
        suggested_channel="AI Jaadugar",
        topics=("AI explained", "AI news", "AI tools and courses"),
    ),
    Group(
        key="science",
        label="Science",
        suggested_channel="Science Jaadugar",
        topics=("science facts", "science experiments"),
    ),
    Group(
        key="programming",
        label="Programming",
        suggested_channel="Code Jaadugar",
        topics=("sql and databases", "programming and coding",
                "developer tools"),
    ),
)

BY_KEY: dict[str, Group] = {g.key: g for g in GROUPS}


def group(key: str) -> Group | None:
    return BY_KEY.get((key or "").strip().lower())


def topics(key: str) -> list[str]:
    found = group(key)
    return list(found.topics) if found else []


def all_topics() -> list[str]:
    """Every topic, in group order. The Topic dropdown's full list."""
    return [t for g in GROUPS for t in g.topics]


def group_for_topic(topic: str) -> Group | None:
    """Which group a topic belongs to.

    Exact match first, then a prefix-word fallback so a topic the user typed
    by hand - "kids dinosaur stories" - still resolves to the kids group and
    therefore to the kids channel. Without the fallback a custom topic maps to
    no channel and silently publishes to the default one.
    """
    wanted = (topic or "").strip().lower()
    if not wanted:
        return None
    for candidate in GROUPS:
        if any(wanted == t.lower() for t in candidate.topics):
            return candidate
    # Free-text fallback: the group's own key or label appearing as a word.
    words = set(wanted.replace("-", " ").replace("/", " ").split())
    for candidate in GROUPS:
        if candidate.key in words or candidate.label.lower() in words:
            return candidate
    return None


def is_child_directed(topic: str) -> bool:
    """True when the TOPIC belongs to a child-directed group.

    Kept here rather than as a list of kids topic names so that adding a
    tenth kids topic cannot forget to add it to a safety list somewhere else.
    """
    found = group_for_topic(topic)
    return bool(found and found.child_directed)


def dump() -> list[dict[str, Any]]:
    """The whole catalogue, for /niche-groups."""
    return [g.to_dict() for g in GROUPS]
