"""The Claude provider, and the guarantee that a model can never bypass validation."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.ai.provider import AnthropicProvider, CoachAnswer, LocalGroundedProvider, RefinedPlan, get_provider


class _FakeMessages:
    """Stands in for client.messages, recording what the provider sent."""

    def __init__(self, result, stop_reason="end_turn"):
        self.result, self.stop_reason, self.calls = result, stop_reason, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed_output=self.result, stop_reason=self.stop_reason,
                               stop_details=SimpleNamespace(category="cyber"))


def _provider(result, stop_reason="end_turn"):
    provider = AnthropicProvider.__new__(AnthropicProvider)   # skip SDK construction
    provider._model = "claude-opus-5"
    provider._client = SimpleNamespace(messages=_FakeMessages(result, stop_reason))
    return provider


SEED = [{
    "action": "create_workout", "scheduled_at": "2026-09-08T18:00:00+00:00", "sport": "running",
    "name": "Threshold intervals", "duration_s": 3600.0, "intensity": "threshold",
    "steps": [{"type": "main", "intensity": "threshold", "duration_s": 3600}], "reason": "seed",
}]


def test_local_provider_is_the_default_and_needs_no_credentials(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_provider", "local")
    assert isinstance(get_provider(), LocalGroundedProvider)


def test_refine_plan_returns_the_models_commands():
    refined = RefinedPlan(
        summary="Reshaped the week", reasoning="Moved the quality session off a back-to-back.",
        commands=[{**SEED[0], "name": "4x8min at threshold", "reason": "better structure"}],
    )
    provider = _provider(refined)
    plan = provider.refine_plan("generate_week", {"as_of": "2026-09-01"}, "seed summary", SEED)

    assert plan.provider == "anthropic"
    assert plan.commands[0]["name"] == "4x8min at threshold"
    assert "Reshaped the week" in plan.summary
    assert "back-to-back" in plan.summary


def test_the_model_is_told_what_it_may_not_do():
    """The system prompt is the contract; if it stops forbidding these, that is a
    behaviour change and this test should fail."""
    provider = _provider(RefinedPlan(summary="s", commands=[SEED[0]]))
    provider.refine_plan("generate_week", {"as_of": "2026-09-01"}, "seed", SEED)
    system = provider._client.messages.calls[0]["system"]
    for forbidden in ("must not", "invent numbers", "add or remove sessions"):
        assert forbidden in system


def test_only_a_narrow_context_slice_is_sent():
    """Raw streams and full history must never leave the application."""
    provider = _provider(RefinedPlan(summary="s", commands=[SEED[0]]))
    context = {
        "as_of": "2026-09-01", "state": {"form": 3}, "profile": {}, "constraints": [],
        "objectives": [], "recent_weeks": [], "sports_28d": {},
        "recent_activities": [{"secret": "should not be sent"}],
        "planned": [{"secret": "nor this"}],
        "thresholds": {"secret": "nor this"},
    }
    provider.refine_plan("generate_week", context, "seed", SEED)
    sent = provider._client.messages.calls[0]["messages"][0]["content"]
    assert "should not be sent" not in sent
    assert "nor this" not in sent
    assert "form" in sent


def test_adaptive_thinking_and_structured_output_are_requested():
    provider = _provider(RefinedPlan(summary="s", commands=[SEED[0]]))
    provider.refine_plan("generate_week", {"as_of": "2026-09-01"}, "seed", SEED)
    call = provider._client.messages.calls[0]
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_format"] is RefinedPlan
    assert call["model"] == "claude-opus-5"


def test_a_refusal_raises_so_the_caller_falls_back():
    """A policy decline is HTTP 200 with stop_reason refusal, so it must be checked
    explicitly rather than read as content."""
    provider = _provider(RefinedPlan(summary="s", commands=[SEED[0]]), stop_reason="refusal")
    with pytest.raises(RuntimeError, match="declined"):
        provider.refine_plan("generate_week", {"as_of": "2026-09-01"}, "seed", SEED)


def test_an_empty_seed_short_circuits_without_calling_the_model():
    provider = _provider(RefinedPlan(summary="s", commands=[]))
    plan = provider.refine_plan("adapt_week", {"as_of": "2026-09-01"}, "nothing to change", [])
    assert plan.commands == []
    assert provider._client.messages.calls == []


def test_analysis_keeps_the_deterministic_evidence_and_numbers():
    draft = {"answer": "raw draft", "evidence": [{"metric": "7-day load", "value": 412.0}],
             "confidence": "high", "facts_only": True}
    provider = _provider(CoachAnswer(answer="Polished answer.", confidence="high"))
    result = provider.synthesize_analysis("Why am I tired?", {"state": {"form": -12}}, draft)

    assert result["answer"] == "Polished answer."
    # Evidence is not the model's to change.
    assert result["evidence"] == draft["evidence"]


# --- the fence: a hostile model still cannot mutate the calendar --------------

def test_a_model_command_still_goes_through_validation(client, session, athlete):
    """Whatever the provider returns is re-validated. A model that tries to touch a
    locked workout, or a workout belonging to someone else, is rejected."""
    from app.ai.commands import validate_commands
    from app.domain.models import PlannedWorkout

    locked = PlannedWorkout(
        athlete_id=athlete.id, scheduled_at=datetime.now(timezone.utc) + timedelta(days=3),
        sport="running", name="Locked key session", duration_s=5400, projected_load=70,
        steps=[{"type": "main", "intensity": "threshold", "duration_s": 5400}], locked=True,
    )
    session.add(locked)
    session.commit()

    hostile = validate_commands(session, athlete.id, [
        {"action": "delete_workout", "workout_id": locked.id},
    ])
    assert hostile["valid"] is False
    assert "locked" in hostile["errors"][0]

    backdated = validate_commands(session, athlete.id, [
        {"action": "create_workout", "scheduled_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
         "sport": "running", "name": "Backdated", "duration_s": 3600, "intensity": "easy"},
    ])
    assert backdated["valid"] is False


def test_provider_failure_falls_back_to_the_deterministic_seed(client, monkeypatch):
    """If the model is unavailable, refuses, or returns junk, the week still gets planned."""
    import app.api.routes as routes

    class Broken:
        name = "anthropic"
        def refine_plan(self, *a, **k): raise RuntimeError("model unavailable")
        def synthesize_analysis(self, *a, **k): raise RuntimeError("model unavailable")

    monkeypatch.setattr(routes, "get_provider", lambda: Broken())
    client.post("/api/v1/objectives", json={
        "name": "Race", "event_date": (date.today() + timedelta(days=90)).isoformat(), "sport": "running"})

    body = client.post("/api/v1/coach/generate-week", json={}).json()
    assert body["provider"] == "local_fallback"
    assert body["validation"]["valid"] is True
    assert body["commands"]

    asked = client.post("/api/v1/coach/ask", json={"question": "Why am I tired this week?"}).json()
    assert asked["answer"]
    assert asked["provider_error"]
