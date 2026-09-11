"""Settings must not report "not connected" over three working channels.

Reported: "though everything is connected but still Youtube connection is
showing not connected and only 1 channel shows there, though under publishing
channels all 3 appears."

Two defects in one endpoint, plus a latent third.

1. `client_source` was derived from the TOP LEVEL of the token file
   (`store.read().get("client_id")`). The client id moved inside
   `channels[<id>]` when the store went multi-channel and this check did not
   move with it - so it found nothing, fell through to `auth.configured`
   (false on a server with no desktop OAuth client) and reported "none".
   The app rendered that as the verdict. The same legacy-top-level-key bug
   was fixed once in `YouTubeAuth.authorized` and missed here.
2. The channel list came from `auth.channels()`, which asks
   `channels.list(mine=True)` with ONE token - and a YouTube token is bound
   to a single channel, so it returns one however many are connected.
   /youtube/accounts was right all along because it reads the store.
3. A single refresh failure flipped `authorized` to false for the whole
   install, so one stale token - or any transient network error - read as
   "reconnect YouTube".
"""
from __future__ import annotations

import inspect
from pathlib import Path

from backend.api import main


SOURCE = inspect.getsource(main.youtube_status)


class TestTheEndpointDescribesTheInstall:
    def test_the_client_source_is_read_from_the_channel_records(self):
        """Not from the top level of the token file, which is where the key
        stopped existing."""
        assert "c.client_id for c in authorised" in SOURCE
        assert 'store.read().get("client_id")' not in SOURCE
        assert 'stored.get("client_id")' not in SOURCE

    def test_the_channel_list_comes_from_the_store(self):
        """One row per AUTHORISATION, like /youtube/accounts - not one row
        per channel a single token happens to see."""
        assert "store.all()" in SOURCE
        assert "channel_count" in SOURCE

    def test_authorised_means_any_channel_can_publish(self):
        """The same rule the upload path uses, so the endpoint and the
        uploader cannot disagree."""
        assert "bool(authorised)" in SOURCE

    def test_one_dead_token_does_not_fail_the_whole_install(self):
        """It used to set authorized=False on any exception from the single
        default-channel fetch."""
        assert 'out["authorized"] = False' not in SOURCE
        # The failure is reported on the channel's own row instead.
        assert 'row["error"]' in SOURCE

    def test_per_channel_stats_are_fetched_per_channel(self):
        assert "channel_id=channel.channel_id" in SOURCE


class TestTheAppRendersTheTruth:
    def _settings(self) -> str:
        return Path("android/app/src/main/java/com/autotube/ai/ui/screens/"
                    "SettingsScreen.kt").read_text(encoding="utf-8")

    def test_the_verdict_is_driven_by_authorised_not_by_client_source(self):
        screen = self._settings()
        # The old line made clientSource the verdict.
        assert 'yt.clientSource != "none"' not in screen
        assert 'ServiceLine(\n                        "YouTube connection",\n' \
               '                        yt.authorized,' in screen

    def test_the_count_is_shown(self):
        assert "yt.channelCount" in self._settings()

    def test_a_channel_that_did_not_answer_does_not_show_zeros(self):
        """"0 subscribers - 0 videos" reads as a real and alarming number
        for a channel whose stats are simply unknown."""
        screen = self._settings()
        assert "channel.error.isBlank()" in screen

    def test_the_dto_carries_the_new_fields(self):
        dtos = Path("android/app/src/main/java/com/autotube/ai/data/remote/"
                    "Dtos.kt").read_text(encoding="utf-8")
        assert '@SerialName("channel_count")' in dtos
        assert '@SerialName("is_default")' in dtos
