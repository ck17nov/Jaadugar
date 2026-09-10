"""The live path and the bank path must not interfere.

Asked for directly: "I should be able to build videos even without script bank
when I want and it should get generated correctly."

So this file pins the SEPARATION rather than either path's behaviour. Every
test here answers one question: with `script_source` left alone, does anything
about the bank reach the render?

The design that makes this cheap: `stage_bank` returns None for a live request
and every bank branch is `if claim is not None`. There is no shared mutable
state between the two, and nothing in the live path reads the bank tables.
"""
from __future__ import annotations

import inspect

import pytest

from engine import pipeline as pipeline_module
from engine.core.models import AutomationRequest, VideoJob
from engine.pipeline import Pipeline


@pytest.fixture()
def pipe(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTUBE_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("AUTOTUBE_DRY_RUN", "1")
    return Pipeline()


def _job(tmp_path) -> VideoJob:
    job = VideoJob(job_id="j1")
    job.dir = str(tmp_path)
    return job


# ---------------------------------------------------------------------------
# The default
# ---------------------------------------------------------------------------
def test_the_default_is_live_generation():
    """A request built with no opinion must not touch the bank.

    This is the whole guarantee. If the default ever flips, every existing
    automation silently changes behaviour.
    """
    assert AutomationRequest().script_source == "live"


def test_stage_bank_is_a_no_op_for_a_live_request(pipe, tmp_path):
    request = AutomationRequest(niche="kids bedtime stories")
    assert pipe.stage_bank(_job(tmp_path), request) is None


def test_a_live_request_never_queries_the_bank(pipe, tmp_path, monkeypatch):
    """Not even a read.

    A SELECT would be harmless today, but it is the seam through which a
    future change could make the live path depend on bank state.
    """
    def explode(*args, **kwargs):               # pragma: no cover
        raise AssertionError("the live path touched the bank")

    monkeypatch.setattr(pipe.db, "claim_bank_entry", explode)
    monkeypatch.setattr(pipe.db, "bank_entries", explode)
    monkeypatch.setattr(pipe.db, "bank_counts", explode)
    assert pipe.stage_bank(_job(tmp_path), AutomationRequest()) is None


def test_a_live_request_keeps_the_duration_it_asked_for(pipe, tmp_path):
    """Only a banked entry may override the requested duration."""
    request = AutomationRequest(niche="kids bedtime stories",
                               duration_seconds=45)
    pipe.stage_bank(_job(tmp_path), request)
    assert request.duration_seconds == 45


# ---------------------------------------------------------------------------
# Structure: every bank branch is gated on a claim
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["stage_idea", "stage_script",
                                    "stage_visuals", "_character_bible"])
def test_every_stage_that_knows_about_the_bank_defaults_to_not_using_it(method):
    """`claim` must be optional and default to None on every stage.

    A required parameter would force every caller - including the CLI, the
    backend and the tests - to know about the bank.
    """
    signature = inspect.signature(getattr(Pipeline, method))
    assert signature.parameters["claim"].default is None


def test_the_bank_modules_are_imported_lazily():
    """The bank must not be a load-time dependency of the pipeline.

    Imported inside the functions that use it, so a deployment with no bank
    and no bank tables still starts.
    """
    source = inspect.getsource(pipeline_module)
    top_level = [line for line in source.splitlines()
                 if line.startswith("from .content") or
                 line.startswith("import")]
    assert not any("bank" in line for line in top_level), \
        f"bank imported at module level: {top_level}"


def test_stage_script_with_no_claim_never_reaches_the_bank_assembler(
        pipe, tmp_path, monkeypatch):
    """The live path must not go through _banked_script.

    Asserted by making the bank assembler fatal rather than by driving the
    whole live stage, which needs a configured LLM.
    """
    from engine.core.models import ContentIdea
    from engine.core.niche import build_profile

    def explode(*args, **kwargs):               # pragma: no cover
        raise AssertionError("the live path used the bank assembler")

    monkeypatch.setattr(pipe, "_banked_script", explode)
    profile = build_profile("science", audience="18-35", language="en",
                            duration_seconds=45)
    # Whatever else happens in the live stage, it is not an AssertionError
    # from the guard above.
    try:
        pipe.stage_script(_job(tmp_path), AutomationRequest(), profile,
                          ContentIdea(topic="t"), "")
    except AssertionError:
        raise
    except Exception:
        pass


# ---------------------------------------------------------------------------
# script_source=bank must FAIL rather than quietly generate
# ---------------------------------------------------------------------------
def test_bank_only_fails_loudly_when_the_bank_is_empty(pipe, tmp_path):
    """A silent fallback would publish an ungated script.

    Someone who chose "use my reviewed scripts" and got a live one has had
    the review guarantee removed without being told.
    """
    request = AutomationRequest(niche="kids bedtime stories",
                                script_source="bank")
    with pytest.raises(pipeline_module.PipelineError) as caught:
        pipe.stage_bank(_job(tmp_path), request)
    assert "no unused reviewed entry" in str(caught.value)


def test_bank_first_falls_back_quietly(pipe, tmp_path):
    """A recurring automation must keep producing after the bank runs dry."""
    request = AutomationRequest(niche="kids bedtime stories",
                                script_source="bank_first")
    assert pipe.stage_bank(_job(tmp_path), request) is None


def test_a_niche_outside_every_group_fails_under_bank_only(pipe, tmp_path):
    request = AutomationRequest(niche="competitive dog grooming",
                                script_source="bank")
    with pytest.raises(pipeline_module.PipelineError) as caught:
        pipe.stage_bank(_job(tmp_path), request)
    assert "channel group" in str(caught.value)


# ---------------------------------------------------------------------------
# One run's writes must not reach the next
# ---------------------------------------------------------------------------
def test_stage_bank_does_write_to_the_request_it_is_given(pipe, tmp_path):
    """Establishes the premise for the two tests below.

    `stage_bank` deliberately overwrites the duration with the entry's own
    length, so the request object it is handed is NOT left alone.
    """
    from tests.test_bank import kids_entry

    pipe.db.save_bank_entry(kids_entry())
    request = AutomationRequest(niche="kids bedtime stories",
                                script_source="bank", duration_seconds=45)
    assert pipe.stage_bank(_job(tmp_path), request) is not None
    assert request.duration_seconds != 45


def test_run_does_not_write_to_the_callers_request(pipe, monkeypatch):
    """The worker reuses ONE request object for every run in `count`.

    So a banked run's duration leaked forward: run 1 claimed a 37-second
    Short and run 2 - a live fallback once the bank ran dry under
    bank_first - was written and rendered for 37 seconds instead of the 45
    that was asked for. `run` works on a copy.
    """
    from tests.test_bank import kids_entry

    pipe.db.save_bank_entry(kids_entry())
    request = AutomationRequest(niche="kids bedtime stories",
                                script_source="bank", duration_seconds=45,
                                made_for_kids=False)

    def stop(*args, **kwargs):
        raise pipeline_module.PipelineError("research", "stopped on purpose")

    monkeypatch.setattr(pipe, "stage_research", stop)
    with pytest.raises(pipeline_module.PipelineError):
        pipe.run(request, skip_preflight=True)

    assert request.duration_seconds == 45
    assert request.made_for_kids is False


def test_the_next_run_in_the_batch_starts_from_the_original(pipe, monkeypatch):
    """The leak end to end: two runs from one request object.

    This is the `count: 2` case the worker actually runs - one request, one
    loop - with the bank running dry between them, which is exactly when
    run 2 has to be paced for the length that was asked for.
    """
    from tests.test_bank import kids_entry

    pipe.db.save_bank_entry(kids_entry())
    request = AutomationRequest(niche="kids bedtime stories",
                                script_source="bank_first",
                                duration_seconds=45)
    seen: list[int] = []

    def record(job, req, *args, **kwargs):
        seen.append(req.duration_seconds)
        raise pipeline_module.PipelineError("research", "stopped on purpose")

    monkeypatch.setattr(pipe, "stage_research", record)
    with pytest.raises(pipeline_module.PipelineError):
        pipe.run(request, skip_preflight=True)

    # A failed run puts its entry BACK, so the bank has to be emptied
    # deliberately to reach the fallback rather than by letting run 1 consume
    # it.
    for row in pipe.db.bank_entries():
        pipe.db.delete_bank_entry(row["entry_id"])
    with pytest.raises(pipeline_module.PipelineError):
        pipe.run(request, skip_preflight=True)

    # Run 1 used the banked entry's length; run 2 found the bank empty and
    # must be back on the requested 45.
    assert seen[0] != 45
    assert seen[1] == 45


# ---------------------------------------------------------------------------
# A failure the operator caused has to be visible on the job
# ---------------------------------------------------------------------------
def test_an_empty_bank_records_FAILED_and_says_why(pipe):
    """It left a job at IDEA with error='' - forever.

    `stage_bank` raised from OUTSIDE run()'s try block, so nothing advanced
    the job row. The API still returned 202 and the app polled a job that
    would never move or explain itself, while the raised message named the
    missing slot exactly.
    """
    from engine.core.models import JobStatus

    request = AutomationRequest(niche="kids bedtime stories",
                                script_source="bank", language="en")
    with pytest.raises(pipeline_module.PipelineError):
        pipe.run(request, skip_preflight=True)

    job = pipe.db.list_jobs(limit=1)[0]
    assert job.status == JobStatus.FAILED.value
    assert "no unused reviewed entry" in job.error


def test_a_stage_error_is_stamped_even_when_the_stage_did_not(pipe,
                                                              monkeypatch):
    """`_advance(job, FAILED, job.error)` logged an empty note."""
    from engine.core.models import JobStatus

    def stop(*args, **kwargs):
        raise pipeline_module.PipelineError("research", "the trend feed died")

    monkeypatch.setattr(pipe, "stage_research", stop)
    with pytest.raises(pipeline_module.PipelineError):
        pipe.run(AutomationRequest(niche="science"), skip_preflight=True)

    job = pipe.db.list_jobs(limit=1)[0]
    assert job.status == JobStatus.FAILED.value
    assert "trend feed died" in job.error
