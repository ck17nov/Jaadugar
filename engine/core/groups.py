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
Three groups instead of twenty-five topics: one tick per channel, and the
three groups are exactly the three channels - Kids, Financial and Technical
Jaadugar. AI, science and programming were separate groups until asked to be
folded into Technical.

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
    # Words that mean "this group" in a topic somebody typed by hand.
    #
    # The fallback used to match the group KEY, which worked only while every
    # subject was its own group. Merging AI, science and programming into
    # "tech" broke it silently: "science of volcanoes" matched nothing, mapped
    # to no channel, and published to the default one. Now that a custom topic
    # is a first-class thing the Create screen offers, this list is what makes
    # it land on the right channel.
    keywords: tuple[str, ...] = field(default_factory=tuple)
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
        keywords=("kids", "kid", "child", "children", "toddler", "baby",
                  "nursery", "bedtime", "rhyme", "rhymes", "story",
                  "stories", "moral", "alphabet", "abc", "counting",
                  "preschool", "kindergarten"),
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
        keywords=("finance", "financial", "money", "invest", "investing",
                  "investment", "mutual", "fund", "funds", "sip", "stock",
                  "stocks", "share", "shares", "tax", "taxes", "insurance",
                  "loan", "loans", "emi", "ppf", "nps", "epf", "savings", "save",
                  "saving", "budget", "budgeting", "credit", "debt",
                  "retirement", "pension", "salary", "income", "upi",
                  "banking", "bank", "gold", "inflation", "interest"),
        topics=("personal finance", "finance news"),
    ),
    # AI, science and programming used to be four separate groups. Merged on
    # request - "i also want to include AI, science and code in Technical. so
    # all their niche/topics should come under technical" - which also puts
    # the group list back in line with the three channels that actually exist:
    # Financial Jaadugar, Kids Jaadugar, Technical Jaadugar.
    Group(
        key="tech",
        label="Technical",
        suggested_channel="Technical Jaadugar",
        # Includes the three merged group names, so a topic typed as "science
        # of volcanoes" or "python programming" still finds this channel.
        keywords=("tech", "technical", "technology", "ai", "ml", "llm",
                  "science", "scientific", "physics", "chemistry", "biology",
                  "space", "astronomy", "programming", "program", "code",
                  "coding", "coder", "developer", "software", "sql",
                  "database", "databases", "python", "java", "javascript",
                  "linux", "windows", "excel", "office", "word",
                  "powerpoint", "spreadsheet", "computer", "pc", "laptop",
                  "phone", "mobile", "gadget", "gadgets", "youtube",
                  "channel", "app", "apps", "server", "cloud", "api"),
        topics=(
            "youtube tips and growth",
            "pc and laptop tech",
            # New phones and laptops. Deliberately NOT called "unboxing": an
            # unboxing claims the presenter has the box, and this channel's
            # narrator is synthetic and has no box. Publishing a fabricated
            # unboxing is the kind of thing YouTube's inauthentic-content
            # policy is aimed at, and it would be a lie about a real product.
            # What IS honest, and covers the same search demand: what the
            # device actually is, what changed, and who it suits.
            "new phone and laptop launches",
            "phone and laptop buying advice",
            # Asked for by name. Screen-recordable, evergreen, and one of the
            # few topics here where the video can show the real thing.
            "excel tips and tricks",
            "ms office tips and tricks",
            "AI explained",
            "AI news",
            "AI tools and courses",
            "science facts",
            "science experiments",
            "sql and databases",
            "programming and coding",
            "developer tools",
        ),
    ),
)

BY_KEY: dict[str, Group] = {g.key: g for g in GROUPS}

# Group keys that no longer exist, and where their topics went.
#
# A brand account's channel mapping is stored BY GROUP KEY, so dropping "ai",
# "science" and "programming" outright would orphan any mapping already made
# against them - the group would resolve to nothing and the video would
# publish to the default channel instead of the technical one. Silent, and
# only visible after the upload.
MERGED_KEYS: dict[str, str] = {
    "ai": "tech",
    "science": "tech",
    "programming": "tech",
    "code": "tech",
    "technical": "tech",
}


def group(key: str) -> Group | None:
    wanted = (key or "").strip().lower()
    found = BY_KEY.get(wanted)
    if found is not None:
        return found
    return BY_KEY.get(MERGED_KEYS.get(wanted, ""))


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

    # Free-text fallback, by keyword count rather than first match. A custom
    # topic can name more than one group - "excel formulas for a home budget"
    # hits both tech and finance - and taking whichever group is declared
    # first in the file is arbitrary. The strongest match wins, and kids wins
    # a tie, because misfiling a children's video as general content loses the
    # stricter safety profile while the reverse only loses monetisation
    # features.
    words = set(wanted.replace("-", " ").replace("/", " ").split())
    best: Group | None = None
    best_hits = 0
    for candidate in GROUPS:
        pool = set(candidate.keywords) | {candidate.key,
                                          candidate.label.lower()}
        hits = len(words & pool)
        if hits > best_hits or (hits == best_hits and hits > 0
                                and candidate.child_directed):
            best, best_hits = candidate, hits
    return best


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
