"""A screen that says "Read-only" must be read-only.

Reported: "options under publishing channels are still exposed and can be
changed without clicking on top edit. and the clear stored credentials is also
exposed same way."

The `editing` flag was added to stop per-keystroke commits of the local
draft, so it only ever gated the six text/slider controls that write to
SecureStore. Everything that mutates something OUTSIDE that draft was live
while the screen displayed "Read-only. Tap Edit to change anything.": the
default channel, the group mapping, removing a channel, adding one,
connecting YouTube, and - one stray tap away - wiping the keystore.

These are text assertions on the Compose source because there is no
instrumentation harness in this repo (android/app/src/test holds two pure
unit tests). A structural check that would catch a regression beats no check.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SETTINGS = Path("android/app/src/main/java/com/autotube/ai/ui/screens/"
                "SettingsScreen.kt")


@pytest.fixture(scope="module")
def screen() -> str:
    return SETTINGS.read_text(encoding="utf-8")


class TestEveryMutatingControlIsGated:
    def test_make_default_is_gated(self, screen):
        assert 'TextButton(onClick = onMakeDefault,\n' \
               '                               enabled = editable)' in screen

    def test_remove_channel_is_gated(self, screen):
        assert 'TextButton(onClick = { confirmForget = true },\n' \
               '                           enabled = editable)' in screen

    def test_the_group_mapping_chips_are_gated(self, screen):
        assert "enabled = editable &&" in screen

    def test_the_group_mapping_rule_survives_the_gate(self, screen):
        """The parentheses matter: dropping them would let one group be
        claimed by two channels, which is the thing this rule exists for."""
        assert "(mine || group.key !in takenElsewhere)" in screen

    def test_connect_youtube_is_gated(self, screen):
        """The OAuth callback writes a refresh token to the backend, so it
        is a mutation whatever the button looks like."""
        assert "enabled = editing && oauthClientId.isNotBlank() && !busy" \
            in screen

    def test_add_channel_is_gated(self, screen):
        assert screen.count(
            "enabled = editing && oauthClientId.isNotBlank() && !busy") == 2

    def test_clear_credentials_is_gated(self, screen):
        assert "OutlinedButton(onClick = { confirmClear = true }, " \
               "enabled = editing)" in screen

    def test_the_channel_card_is_told_whether_it_is_editable(self, screen):
        assert "editable: Boolean," in screen
        assert "editable = editing," in screen


class TestDestructiveActionsAsk:
    def test_clearing_credentials_needs_a_confirmation(self, screen):
        assert "confirmClear" in screen
        assert 'title = { Text("Clear stored credentials?") }' in screen

    def test_the_confirmation_survives_a_rotation(self, screen):
        """remember, not rememberSaveable, would drop the dialog on
        rotation - with the destructive action still one tap away."""
        assert "var confirmClear by rememberSaveable" in screen
        assert "var confirmForget by rememberSaveable" in screen

    def test_the_drafts_are_reset_when_the_clear_actually_happens(self, screen):
        """Otherwise the cleared key stays on screen and the next Save
        writes it straight back."""
        block = screen.split("vm.clearSecrets()", 1)[1][:1400]
        for field in ('apiKey = ""', 'oauthClientId = ""', 'ytAccount = ""'):
            assert field in block, field

    def test_the_backend_url_is_not_blanked(self, screen):
        """clearSecrets() does NOT remove KEY_BACKEND_URL, so resetting the
        draft made the field disagree with storage and armed the next Save
        to wipe a working URL."""
        block = screen.split("vm.clearSecrets()", 1)[1][:1400]
        assert 'backendUrl = ""' not in block
        # And the dialog says so, rather than leaving the scope implied.
        assert "The backend URL is kept" in screen

    def test_clear_secrets_really_does_leave_the_url_alone(self):
        """The assertion above is only meaningful if this stays true."""
        store = Path("android/app/src/main/java/com/autotube/ai/data/prefs/"
                     "SecureStore.kt").read_text(encoding="utf-8")
        body = store.split("fun clearSecrets()", 1)[1].split("}", 1)[0]
        assert "KEY_BACKEND_URL" not in body


class TestReadOnlyActionsStayAvailable:
    """Gating these would be the opposite regression: a read-only operator
    still has to be able to diagnose the backend."""

    def test_refresh_and_reload_are_not_behind_edit(self, screen):
        assert "OutlinedButton(onClick = { vm.refreshYouTube() },\n" \
               "                           enabled = !busy)" in screen
        assert "OutlinedButton(onClick = { vm.refreshAccounts() },\n" \
               "                           enabled = !busy)" in screen

    def test_choose_groups_is_not_gated(self, screen):
        """It expands a disclosure and mutates nothing - it is how a
        read-only user inspects the mapping."""
        assert "TextButton(onClick = { showNiches = !showNiches })" in screen


def test_the_caption_matches_the_behaviour(screen):
    """The old caption - "Read-only. Tap Edit to change anything." - was
    false while it was on screen."""
    assert "Read-only, including the publishing channels below" in screen
