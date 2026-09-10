"""Channel groups: one tick per channel instead of nine.

A YouTube token is bound to one channel, so something must decide which video
goes where. That was a per-TOPIC mapping, and nine of the twenty-one topics
begin with "kids" - so setting up a kids channel meant nine ticks and
forgetting one sent that topic silently to the default channel.
"""
from __future__ import annotations

import json

import pytest

from engine.core.groups import (GROUPS, all_topics, dump, group,
                                group_for_topic, is_child_directed, topics)
from engine.youtube.channels import Channel, ChannelStore


class _Tokens:
    """A TokenStore standing in for the real 0600 file."""

    def __init__(self, data=None):
        self._data = data or {}

    def read(self):
        return json.loads(json.dumps(self._data))

    def write(self, data):
        self._data = json.loads(json.dumps(data))


def _store(*channels):
    store = ChannelStore(_Tokens())
    for channel in channels:
        store.put(channel)
    return store


class TestCatalogue:
    def test_every_group_has_topics_and_a_channel_name(self):
        for g in GROUPS:
            assert g.topics, g.key
            assert g.suggested_channel, g.key
            assert g.label, g.key

    def test_no_topic_belongs_to_two_groups(self):
        """Otherwise "which channel does this go to" has two answers."""
        seen = {}
        for g in GROUPS:
            for topic in g.topics:
                assert topic.lower() not in seen, (topic, g.key, seen.get(topic.lower()))
                seen[topic.lower()] = g.key

    def test_only_the_kids_group_is_child_directed(self):
        flagged = [g.key for g in GROUPS if g.child_directed]
        assert flagged == ["kids"]

    def test_all_topics_is_every_group_concatenated(self):
        assert len(all_topics()) == sum(len(g.topics) for g in GROUPS)

    def test_the_dump_is_json_serialisable(self):
        """It is served over HTTP to the app."""
        json.dumps(dump())


class TestTopicToGroup:
    def test_an_exact_topic_resolves(self):
        assert group_for_topic("kids bedtime stories").key == "kids"
        # AI, science and programming were folded into "tech" on request.
        assert group_for_topic("sql and databases").key == "tech"
        assert group_for_topic("AI explained").key == "tech"
        assert group_for_topic("science facts").key == "tech"
        assert group_for_topic("excel tips and tricks").key == "tech"

    def test_case_and_spacing_do_not_matter(self):
        assert group_for_topic("  KIDS Bedtime Stories ").key == "kids"

    def test_a_hand_typed_topic_still_finds_its_group(self):
        """Without this a custom topic maps to no channel and publishes to
        the default one, which for a kids video is the wrong channel AND the
        wrong safety profile."""
        assert group_for_topic("kids dinosaur stories").key == "kids"
        # The fallback used to match the group KEY, so merging the science
        # group into "tech" silently broke every hand-typed science topic.
        assert group_for_topic("science of volcanoes").key == "tech"
        assert group_for_topic("python programming").key == "tech"
        assert group_for_topic("best laptop under 50000").key == "tech"
        assert group_for_topic("mutual fund basics").key == "finance"
        assert group_for_topic("tax saving tips").key == "finance"
        assert group_for_topic("bedtime story about a dragon").key == "kids"

    def test_an_unrelated_topic_resolves_to_nothing(self):
        assert group_for_topic("cricket highlights") is None
        assert group_for_topic("") is None

    def test_child_directed_follows_the_group(self):
        assert is_child_directed("kids toys and play") is True
        assert is_child_directed("kids dinosaur stories") is True
        assert is_child_directed("finance news") is False

    def test_lookup_helpers(self):
        assert group("kids").label == "Kids"
        assert group("nope") is None

    def test_retired_group_keys_still_resolve(self):
        """A brand account's channel mapping is stored BY GROUP KEY.

        Dropping "ai", "science" and "programming" outright would orphan any
        mapping already made against them - the group would resolve to
        nothing and the video would publish to the default channel instead of
        the technical one. Silent, and only visible after the upload.
        """
        for retired in ("ai", "science", "programming", "code"):
            assert group(retired).key == "tech", retired

    def test_the_groups_are_the_channels(self):
        """Three groups, three brand channels, one tick each."""
        assert [g.key for g in GROUPS] == ["kids", "finance", "tech"]
        assert [g.suggested_channel for g in GROUPS] == [
            "Kids Jaadugar", "Financial Jaadugar", "Technical Jaadugar"]

    def test_every_group_has_fallback_keywords(self):
        """Without them a custom topic in that group maps to no channel."""
        for candidate in GROUPS:
            assert candidate.keywords, candidate.key

    def test_kids_wins_a_keyword_tie(self):
        """Misfiling a children's video loses the stricter safety profile.

        The reverse only loses monetisation features, so the tie-break is
        deliberately asymmetric.
        """
        assert group_for_topic("kids science experiments").key == "kids"
        assert "finance news" in topics("finance")
        assert topics("nope") == []


class TestChannelResolution:
    def _channel(self, cid, niches):
        return Channel(channel_id=cid, title=cid, refresh_token="t",
                       niches=list(niches))

    def test_a_group_mapping_covers_every_topic_in_it(self):
        """The whole point: one tick, nine topics."""
        store = _store(self._channel("kids-ch", ["kids"]))
        for topic in topics("kids"):
            found = store.for_niche(topic)
            assert found is not None and found.channel_id == "kids-ch", topic

    def test_groups_route_to_different_channels(self):
        store = _store(self._channel("kids-ch", ["kids"]),
                       self._channel("money-ch", ["finance"]))
        assert store.for_niche("kids rhymes and poems").channel_id == "kids-ch"
        assert store.for_niche("personal finance").channel_id == "money-ch"

    def test_an_exact_topic_mapping_beats_its_group(self):
        """A deliberate one-topic override must win, and an existing install
        that mapped individual topics has to keep working."""
        store = _store(self._channel("kids-ch", ["kids"]),
                       self._channel("rhymes-ch", ["kids rhymes and poems"]))
        assert store.for_niche("kids rhymes and poems").channel_id == "rhymes-ch"
        assert store.for_niche("kids moral stories").channel_id == "kids-ch"

    def test_legacy_per_topic_mappings_still_resolve(self):
        store = _store(self._channel("old-ch", ["kids bedtime stories"]))
        assert store.for_niche("kids bedtime stories").channel_id == "old-ch"

    def test_an_unmapped_topic_returns_none(self):
        """The caller then uses the default channel - deliberately, rather
        than guessing."""
        store = _store(self._channel("kids-ch", ["kids"]))
        assert store.for_niche("personal finance") is None


class TestWhatTheAppIsOffered:
    """The catalogue the Create screen renders, as served.

    Reported as "I still see science/code/AI in channel group" - which was
    the app's OFFLINE fallback, shown because the backend was not running.
    These pin the served list, so if the retired groups ever reappear there
    it is a real regression rather than a connectivity symptom.
    """

    def test_exactly_three_groups_are_offered(self):
        served = dump()
        assert [g["key"] for g in served] == ["kids", "finance", "tech"]

    def test_no_retired_group_is_offered(self):
        """`MERGED_KEYS` must resolve them without LISTING them."""
        from engine.core.groups import MERGED_KEYS

        offered = {g["key"] for g in dump()}
        for retired in MERGED_KEYS:
            if retired == "tech":
                continue
            assert retired not in offered, f"{retired} is still offered"
            # Still resolvable, so a saved automation does not break.
            assert group(retired) is not None

    @pytest.mark.parametrize("topic", [
        "AI explained", "AI news", "AI tools and courses",
        "science facts", "science experiments",
        "sql and databases", "programming and coding", "developer tools",
    ])
    def test_every_ai_science_and_code_topic_sits_under_technical(self, topic):
        served = {g["key"]: g["topics"] for g in dump()}
        assert topic in served["tech"], f"{topic} is not under Technical"

    @pytest.mark.parametrize("topic", [
        "excel tips and tricks",
        "ms office tips and tricks",
        "new phone and laptop launches",
        "phone and laptop buying advice",
    ])
    def test_the_requested_additions_are_offered(self, topic):
        """Asked for: Excel/MS Office tips, and phone/laptop launches."""
        served = {g["key"]: g["topics"] for g in dump()}
        assert topic in served["tech"], f"{topic} is missing from Technical"

    def test_the_technical_label_is_what_the_screen_shows(self):
        served = {g["key"]: g for g in dump()}
        assert served["tech"]["label"] == "Technical"
