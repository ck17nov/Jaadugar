"""Seeded-channel research: 1 + 2N units instead of ~302.

`search.list` costs 100 units of a 10,000 daily budget, so keyword research
could run three times a day and then stop. Watching a fixed set of channels
costs one `channels.list` plus one `playlistItems.list` per channel plus one
`videos.list` per fifty ids - measured live at 9 units for four channels and
180 videos, against 302.
"""
from __future__ import annotations

import inspect
import json

import pytest

from engine.content.metadata import title_patterns
from engine.core.config import load_config
from engine.research.youtube import YouTubeResearch, _keywords


def _code(obj) -> str:
    """A function's source with the # comments removed.

    Asserting against raw source matches the COMMENTS too, so a note saying
    "profile.audience was removed because..." fails a test asserting that
    profile.audience is absent. This project has been bitten by that twice.
    """
    import re as _re
    lines = []
    for line in inspect.getsource(obj).split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(_re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(lines)


class _Video:
    """Stands in for ResearchVideo - title_patterns only reads two fields."""

    def __init__(self, title, views):
        self.title = title
        self.views = views


class TestQuotaArithmetic:
    def test_reading_a_playlist_is_one_unit(self):
        """The whole saving rests on this being 1 and search.list being 100."""
        research = YouTubeResearch(load_config())
        assert research.quota.cost("playlist_items") == 1
        assert research.quota.cost("channels_list") == 1
        assert research.quota.cost("videos_list") == 1
        assert research.quota.cost("search_list") == 100

    def test_the_cheap_path_spends_no_search_units_once_seeded(self):
        """research_channels must not call search.list - that is the point.

        Seed discovery does, once per niche, and caches the answer.
        """
        code = _code(YouTubeResearch.research_channels)
        # The API CALL, not the word: the docstring mentions keyword search.
        assert '_get("search"' not in code
        assert "seed_channels" in code


class TestSeedCaching:
    def test_seeds_round_trip_to_disk(self, tmp_path, monkeypatch):
        cfg = load_config()
        cfg.set("paths.workspace", str(tmp_path))
        research = YouTubeResearch(cfg)
        monkeypatch.setattr(research, "_seed_path",
                            lambda: tmp_path / "seed_channels.json")
        research._save_seeds({"kids bedtime stories": ["UC_a", "UC_b"]})
        assert research._load_seeds() == {"kids bedtime stories": ["UC_a", "UC_b"]}

    def test_a_missing_file_is_not_an_error(self, tmp_path, monkeypatch):
        research = YouTubeResearch(load_config())
        monkeypatch.setattr(research, "_seed_path", lambda: tmp_path / "none.json")
        assert research._load_seeds() == {}

    def test_a_corrupt_file_is_not_an_error(self, tmp_path, monkeypatch):
        bad = tmp_path / "seed_channels.json"
        bad.write_text("{not json", encoding="utf-8")
        research = YouTubeResearch(load_config())
        monkeypatch.setattr(research, "_seed_path", lambda: bad)
        assert research._load_seeds() == {}

    def test_a_cached_seed_costs_nothing(self, tmp_path, monkeypatch):
        """A cached hit must not spend the 100-unit discovery again."""
        research = YouTubeResearch(load_config())
        path = tmp_path / "seed_channels.json"
        path.write_text(json.dumps({"kids bedtime stories": ["UC_x"]}),
                        encoding="utf-8")
        monkeypatch.setattr(research, "_seed_path", lambda: path)
        before = research.quota.used()

        from engine.core.niche import build_profile
        profile = build_profile("kids bedtime stories", audience="5-7",
                                made_for_kids=True)
        assert research.seed_channels("kids bedtime stories", profile) == ["UC_x"]
        assert research.quota.used() == before


class TestSeedRelevance:
    def test_the_query_is_the_niche_alone(self):
        """Including profile.audience put "5-7" in the query, and "kids
        bedtime stories 5-7" seeded a comedy channel whose top video was
        "Indian Schools, Gully Cricket & Summer Holidays" at 39M views. That
        result is CACHED, so a bad seed poisons every later run."""
        assert "profile.audience" not in _code(YouTubeResearch.seed_channels)

    def test_candidates_are_validated_before_caching(self):
        assert "_keep_relevant" in _code(YouTubeResearch.seed_channels)

    def test_relevance_is_judged_on_uploads_not_descriptions(self):
        """A channel description is marketing; its titles are what it makes."""
        source = inspect.getsource(YouTubeResearch._keep_relevant)
        assert "_playlist_video_ids" in source
        assert "title" in source

    @pytest.mark.parametrize("niche,expected", [
        ("kids bedtime stories", {"kids", "bedtime", "stories"}),
        ("sql and databases", {"sql", "databases"}),
        ("pc and laptop tech", {"laptop", "tech"}),
    ])
    def test_niche_keywords_drop_stopwords(self, niche, expected):
        assert expected <= set(_keywords(niche))


class TestTitlePatterns:
    def _corpus(self):
        return [_Video("Sleep Meditation for Kids THE DREAMY KITTEN", 898_164),
                _Video("BEST Bedtime Story in The World", 831_555),
                _Video("The Sleepy Autumn Train", 18_052)]

    def test_it_is_sorted_by_performance(self):
        block = title_patterns(self._corpus())
        assert block.index("DREAMY KITTEN") < block.index("Sleepy Autumn")

    def test_view_counts_are_shown(self):
        """The number is the signal - without it every example looks equal."""
        assert "898,164 views" in title_patterns(self._corpus())

    def test_it_states_the_originality_boundary(self):
        """"Here are competitor titles" is exactly the input that invites
        copying, so the instruction says so in words rather than relying on
        the model's judgement."""
        block = title_patterns(self._corpus())
        assert "Do NOT copy" in block
        assert "Borrow the pattern, never the content." in block

    def test_it_asks_for_shape_not_content(self):
        assert "Learn the SHAPE" in title_patterns(self._corpus())

    def test_an_empty_corpus_is_a_no_op(self):
        """Research can legitimately return nothing - quota, no key, a new
        niche - and the prompt must not grow an empty section."""
        assert title_patterns([]) == ""
        assert title_patterns(None) == ""

    def test_near_duplicates_are_dropped(self):
        """Channels re-upload the same title with a suffix, and twelve slots
        spent on one title teaches the model nothing. Keyed on the first 40
        characters, so titles that genuinely differ early are both kept."""
        corpus = [
            _Video("Sleep Meditation for Kids THE DREAMY KITTEN part 1", 100),
            _Video("Sleep Meditation for Kids THE DREAMY KITTEN part 2", 90),
            _Video("A completely different bedtime story title", 80)]
        block = title_patterns(corpus)
        assert block.count("THE DREAMY KITTEN") == 1
        assert "completely different" in block

    def test_the_list_is_capped(self):
        corpus = [_Video(f"Distinct title {i}", 1000 - i) for i in range(40)]
        assert title_patterns(corpus, limit=6).count("  - ") == 6

    def test_titles_without_text_are_skipped(self):
        assert title_patterns([_Video("", 999)]) == ""


class TestWiring:
    def test_the_title_prompt_receives_the_corpus(self):
        """It received NO research at all, which is why titles were generic -
        the prompt had nothing to be attractive against."""
        from engine.content.metadata import MetadataGenerator
        source = inspect.getsource(MetadataGenerator._title_prompt)
        assert "title_patterns(research)" in source

    def test_the_pipeline_passes_research_to_metadata(self):
        from engine import pipeline
        source = inspect.getsource(pipeline.Pipeline.stage_finalize)
        assert "research=videos" in source
