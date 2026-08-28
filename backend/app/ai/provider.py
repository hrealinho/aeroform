from __future__ import annotations
from dataclasses import dataclass
import json

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings

DEFAULT_CLAUDE_MODEL = "claude-opus-5"


class RefinedCommand(BaseModel):
    """Mirrors PlanCommand. The application re-validates against that schema anyway, but
    constraining the model's output means it cannot return a shape that fails outright."""

    action: str = Field(pattern="^(create_workout|update_workout|move_workout|delete_workout)$")
    workout_id: int | None = None
    scheduled_at: str | None = None
    sport: str | None = None
    name: str | None = None
    duration_s: float | None = None
    elevation_m: float | None = None
    intensity: str | None = None
    steps: list[dict] | None = None
    objective_id: int | None = None
    block_id: int | None = None
    reason: str | None = None


class RefinedPlan(BaseModel):
    summary: str
    reasoning: str | None = None
    commands: list[RefinedCommand]


class CoachAnswer(BaseModel):
    answer: str
    confidence: str | None = None


@dataclass
class ProviderPlan:
    summary: str
    commands: list[dict]
    provider: str


class LocalGroundedProvider:
    """Deterministic provider used by default and in tests.

    It proves the AI orchestration path without requiring secrets and ensures the
    application remains useful when no remote model is configured.
    """
    name = "local"

    def synthesize_analysis(self, question: str, context: dict, draft: dict) -> dict:
        return draft

    def refine_plan(self, kind: str, context: dict, seed_summary: str, seed_commands: list[dict]) -> ProviderPlan:
        return ProviderPlan(seed_summary, seed_commands, self.name)


class HttpJsonProvider:
    """Minimal vendor-neutral JSON hook.

    POSTs {kind, model, context, draft/seed_commands}. A compatible endpoint may
    return {answer} for analysis or {summary, commands} for planning. Commands are
    always validated by the application before they become an AIProposal.
    """
    name = "http_json"

    def _post(self, payload: dict) -> dict:
        if not settings.ai_endpoint:
            raise RuntimeError("AI_ENDPOINT is required for ai_provider=http_json")
        headers = {"Content-Type": "application/json"}
        if settings.ai_api_key:
            headers["Authorization"] = f"Bearer {settings.ai_api_key}"
        with httpx.Client(timeout=settings.ai_timeout_s) as client:
            response = client.post(settings.ai_endpoint, headers=headers, json={"model": settings.ai_model, **payload})
            response.raise_for_status()
            return response.json()

    def synthesize_analysis(self, question: str, context: dict, draft: dict) -> dict:
        data = self._post({"kind": "analysis", "question": question, "context": context, "draft": draft})
        if not isinstance(data.get("answer"), str):
            raise RuntimeError("AI endpoint must return an answer string")
        return {**draft, "answer": data["answer"], "provider_metadata": data.get("metadata")}

    def refine_plan(self, kind: str, context: dict, seed_summary: str, seed_commands: list[dict]) -> ProviderPlan:
        data = self._post({"kind": kind, "context": context, "seed_summary": seed_summary, "seed_commands": seed_commands})
        commands = data.get("commands", seed_commands)
        if not isinstance(commands, list):
            raise RuntimeError("AI endpoint commands must be a list")
        return ProviderPlan(str(data.get("summary") or seed_summary), commands, self.name)




# ---------------------------------------------------------------------------
# Claude provider
# ---------------------------------------------------------------------------
# What the model is and is not allowed to do is the whole point of this class.
#
# It never computes load, never decides thresholds, and never writes to the
# calendar. The deterministic planner produces a seed - the right weekly volume,
# the right phase, days that respect availability - and the model reshapes that
# seed: session ordering, workout design, intensity distribution, naming and
# rationale. Whatever comes back is re-validated by validate_commands before it
# can become a proposal, and again before it is applied.
#
# That division matters for cost as well as trust: the expensive reasoning is
# already done deterministically, so the model handles one small structured
# request per plan rather than being asked to be the metrics engine.

PLANNER_SYSTEM = """You are an endurance coaching assistant inside a training platform.

A deterministic planning engine has already decided the correct weekly volume, the training
phase, and which days are available. Your job is to improve the QUALITY of the prescription
within that envelope, not to change the envelope.

You may:
- redesign workout structure (warm-up, intervals, recovery, cool-down) to better serve the phase
- reorder or re-space sessions across the days already offered, to improve recovery spacing
- adjust the intensity label of a session when the distribution is poorly balanced
- rewrite names and reasons so the athlete understands the intent

You must not:
- change total weekly duration by more than 10% from the seed
- move a session onto a date not present in the seed
- add or remove sessions
- invent numbers for load, TSS, FTP, heart-rate zones or thresholds; the platform computes those
- schedule two hard sessions on consecutive days unless the phase is peak and the athlete's
  recent adherence supports it

Every command you return must keep the same action and, for updates, the same workout_id as the
seed. Explain your reasoning in terms of training principles, not in terms of the numbers you
were given."""


class AnthropicProvider:
    """Refines a deterministic plan seed with Claude.

    Structured outputs are used rather than free text: the model returns a schema the
    application already validates, so there is no JSON parsing to get wrong.
    """

    name = "anthropic"

    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise RuntimeError("AI_PROVIDER=anthropic requires the 'anthropic' package") from exc
        # The SDK resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an `ant auth login`
        # profile on its own; only pass a key when one is configured explicitly.
        self._client = anthropic.Anthropic(api_key=settings.ai_api_key) if settings.ai_api_key else anthropic.Anthropic()
        self._model = settings.ai_model if settings.ai_model.startswith("claude-") else DEFAULT_CLAUDE_MODEL

    def _parse(self, output_model, system: str, prompt: str):
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            system=system,
            # Adaptive thinking: planning is a reasoning task and the model decides depth.
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
            output_format=output_model,
        )
        # A policy decline arrives as HTTP 200 with stop_reason "refusal", so it has to be
        # checked before reading content. Raising here makes the caller fall back to the
        # deterministic seed, which is always a valid plan.
        if getattr(response, "stop_reason", None) == "refusal":
            detail = getattr(response, "stop_details", None)
            raise RuntimeError(f"Model declined the request ({getattr(detail, 'category', 'unknown')})")
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise RuntimeError("Model returned no structured output")
        return parsed

    def synthesize_analysis(self, question: str, context: dict, draft: dict) -> dict:
        result = self._parse(
            CoachAnswer,
            "You are an endurance coach. Rewrite the supplied draft answer so it reads clearly and "
            "directly to an athlete. Keep every number exactly as given - they were computed from the "
            "athlete's data and you must not alter, round or add to them. Do not introduce facts that "
            "are not in the draft or the context.",
            json.dumps({"question": question, "draft": draft, "context": _analysis_context(context)}, default=str),
        )
        # Evidence stays exactly as the deterministic layer produced it.
        return {**draft, "answer": result.answer, "confidence": result.confidence or draft.get("confidence", "medium")}

    def refine_plan(self, kind: str, context: dict, seed_summary: str, seed_commands: list[dict]) -> ProviderPlan:
        if not seed_commands:
            return ProviderPlan(seed_summary, seed_commands, self.name)
        result = self._parse(
            RefinedPlan,
            PLANNER_SYSTEM,
            json.dumps({
                "task": kind,
                "seed_summary": seed_summary,
                "seed_commands": seed_commands,
                "athlete": _planning_context(context),
            }, default=str),
        )
        commands = [c.model_dump(exclude_none=True) for c in result.commands]
        summary = result.summary or seed_summary
        if result.reasoning:
            summary = f"{summary} {result.reasoning}"
        return ProviderPlan(summary, commands, self.name)


def _planning_context(context: dict) -> dict:
    """The subset of athlete context a planner needs.

    Deliberately narrow: raw streams and the full history are never sent, and a smaller
    payload is both cheaper and easier for the model to use well.
    """
    return {
        "as_of": context.get("as_of"),
        "state": context.get("state"),
        "current_block": context.get("current_block"),
        "objectives": (context.get("objectives") or [])[:3],
        "profile": context.get("profile"),
        "constraints": context.get("constraints"),
        "recent_weeks": (context.get("recent_weeks") or [])[-6:],
        "adherence_28d_pct": context.get("adherence_28d_pct"),
        "sports_28d": context.get("sports_28d"),
    }


def _analysis_context(context: dict) -> dict:
    return {
        "state": context.get("state"),
        "recent_weeks": (context.get("recent_weeks") or [])[-6:],
        "objectives": (context.get("objectives") or [])[:2],
    }


def get_provider():
    if settings.ai_provider == "anthropic":
        return AnthropicProvider()
    if settings.ai_provider == "http_json":
        return HttpJsonProvider()
    return LocalGroundedProvider()
