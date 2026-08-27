from __future__ import annotations
from dataclasses import dataclass
import httpx
from app.core.config import settings


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


def get_provider():
    return HttpJsonProvider() if settings.ai_provider == "http_json" else LocalGroundedProvider()
