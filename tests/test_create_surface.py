"""What the Create screen is told, and whether the backend agrees.

The same class of defect as tests/test_bank_surface.py, one layer up: the
interface promising something the render would not do. Each of these was
verified against the real endpoint before it was fixed.
"""
from __future__ import annotations

import inspect
import os

import pytest

TEST_TOKEN = "test-token-do-not-use-in-production-0123456789"
os.environ.setdefault("AUTOTUBE_API_TOKEN", TEST_TOKEN)
os.environ.setdefault("DRY_RUN", "true")

from fastapi.testclient import TestClient          # noqa: E402

from backend.api.main import app                   # noqa: E402

AUTH = {"X-API-Key": os.environ["AUTOTUBE_API_TOKEN"]}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# The bank count must be taken over the group a claim would use
# ---------------------------------------------------------------------------
def test_a_blank_group_is_resolved_from_the_topic(client):
    """It reported zero, and the screen then warned the run would FAIL.

    A blank group went straight into `grp = ?`, which matches no row, so the
    count was structurally zero - while `stage_bank` resolves a blank group
    from the topic before claiming. The app's default "All topics" selection
    therefore always read ready=0, and told the operator to import a batch
    they already had.
    """
    response = client.get("/script-bank", headers=AUTH, params={
        "language": "en", "video_format": "LONGFORM",
        "topic": "excel tips and tricks"})
    assert response.status_code == 200
    query = response.json().get("query")
    assert query is not None
    assert query["resolved_group"] == "tech"


def test_an_explicit_group_is_not_second_guessed(client):
    response = client.get("/script-bank", headers=AUTH, params={
        "group": "kids", "language": "hi", "video_format": "SHORT"})
    assert response.json()["query"]["resolved_group"] == "kids"


def test_an_unmatched_topic_resolves_to_nothing_rather_than_guessing(client):
    response = client.get("/script-bank", headers=AUTH, params={
        "language": "en", "video_format": "SHORT",
        "topic": "competitive dog grooming"})
    query = response.json()["query"]
    # No group claimed it, so the count is over the whole bank rather than a
    # made-up group - and the caller can see that.
    assert query["resolved_group"] == ""


# ---------------------------------------------------------------------------
# Child-directed is a property of the CHANNEL, not only of the words
# ---------------------------------------------------------------------------
def test_a_custom_topic_under_the_kids_group_needs_confirmation(client):
    """It published as general-audience content.

    `is_kids_niche` matches the niche STRING, so "story of the thirsty crow"
    was not child-directed as far as this gate was concerned - and with the
    age band set to "all ages" the app cleared made_for_kids too. The video
    went to the kids channel without the Made-for-Kids classification and was
    built with none of the kids restrictions.
    """
    response = client.post("/automations", headers=AUTH, json={
        "niche": "story of the thirsty crow",
        "niche_group": "kids",
        "made_for_kids": False,
        "audience": "all ages",
    })
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "kids_confirmation_required"
    assert "group" in detail["because"]


def test_a_custom_topic_under_a_general_group_is_left_alone(client):
    """The gate must not start refusing ordinary requests."""
    response = client.post("/automations", headers=AUTH, json={
        "niche": "how a sinking fund works for school fees",
        "niche_group": "finance",
        "made_for_kids": False,
    })
    # 202 accepted, or 503 when this machine has no ffmpeg / API key. The one
    # thing it must not be is the kids gate.
    assert response.status_code != 409, response.text
    assert response.status_code in (202, 503), response.text


def test_the_preview_asks_the_kids_question_for_a_kids_group(client):
    response = client.get("/niche/preview", headers=AUTH, params={
        "niche": "story of the thirsty crow", "group": "kids"})
    body = response.json()
    assert body["requires_kids_confirmation"] is True
    assert body["child_directed_group"] is True
    # The niche STRING alone still does not look child-directed, which is
    # exactly why the group had to be consulted.
    assert body["kids_niche_detected"] is False


def test_the_preview_applies_the_kids_profile_to_a_custom_topic(client):
    """Not just the flag: the restrictions have to come with it."""
    body = client.get("/niche/preview", headers=AUTH, params={
        "niche": "story of the thirsty crow", "group": "kids"}).json()
    assert body["profile"]["made_for_kids"] is True
    assert body["profile"]["restrictions"]


# ---------------------------------------------------------------------------
# Do not promise captions a template will not burn in
# ---------------------------------------------------------------------------
def test_the_preview_reports_when_no_captions_will_be_burned_in(client):
    """The screen said "captions will be in Hindi" over a video with none.

    The long-form illustrated explainer deliberately burns none in, and the
    caption line was unconditional.
    """
    body = client.get("/niche/preview", headers=AUTH, params={
        "niche": "mutual funds explained", "style": "storytelling",
        "video_format": "LONGFORM", "language": "en"}).json()
    assert body["caption_style"] == "none"
    assert body["caption_language"] == ""


def test_the_preview_reports_the_caption_language_when_there_will_be_some(
        client):
    body = client.get("/niche/preview", headers=AUTH, params={
        "niche": "kids bedtime stories", "group": "kids",
        "video_format": "SHORT", "language": "hi"}).json()
    assert body["caption_style"] == "block"
    assert body["caption_language"] == "en"


# ---------------------------------------------------------------------------
# The schedule list has to carry enough to rebuild a run
# ---------------------------------------------------------------------------
def test_the_automation_list_serves_the_whole_request():
    """The app rebuilds each recurring run from its own Room row, and Room's
    migration is destructive - so an upgrade emptied the table and every
    recurring automation cancelled itself on its next wake. These fields are
    what let the app restore the rows from the backend.
    """
    from backend.api.main import list_automations

    source = inspect.getsource(list_automations)
    for field in ("audience", "style", "duration_seconds", "voice_gender",
                  "caption_language", "caption_style", "publish_mode",
                  "script_source", "niche_group", "min_quality_score"):
        assert f'"{field}"' in source, f"/automations does not serve {field}"


def test_the_schedule_resolves_the_channel_with_the_chosen_group():
    """It omitted the group, so a custom topic named the wrong channel."""
    from backend.api.main import _automation_target

    source = inspect.getsource(_automation_target)
    assert "group.key if group else" in source, \
        "_automation_target must pass the resolved group to for_niche"
