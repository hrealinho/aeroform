from __future__ import annotations


def _pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return (current / previous - 1.0) * 100.0


def analyse_question(context: dict, question: str) -> dict:
    """Produce a grounded draft answer from structured context.

    This is intentionally deterministic. A remote LLM may improve phrasing, but
    the facts/evidence come from this layer and are returned separately.
    """
    q = question.lower()
    state = context["state"]
    evidence: list[dict] = []
    confidence = "high" if context["history"]["activity_count"] >= 20 else "medium"

    if any(word in q for word in ("tired", "fatigue", "fatigued", "recovery", "fresh")):
        typical = float(state.get("typical_weekly_load_8w") or 0)
        load7 = float(state.get("load_7d") or 0)
        change = _pct_change(load7, typical)
        evidence += [
            {"metric": "7-day load", "value": round(load7, 1), "period": "last 7 days"},
            {"metric": "typical weekly load", "value": round(typical, 1), "period": "median of recent completed weeks"},
            {"metric": "form", "value": state.get("form"), "period": context["as_of"]},
        ]
        if change is not None and change > 15:
            answer = f"Your recent training load is materially above your recent norm: {load7:.0f} over the last 7 days versus a typical week of about {typical:.0f} ({change:.0f}% higher). Your current form is {state.get('form', 0):.1f}. That combination is a plausible training-load explanation for feeling less fresh."
        elif float(state.get("form") or 0) < -15:
            answer = f"Your weekly load is not dramatically above your norm, but your current form is {state.get('form', 0):.1f}, which indicates short-term fatigue is elevated relative to longer-term fitness. I would treat that as a reason to protect recovery before adding extra intensity."
        else:
            answer = "The load metrics do not show a large obvious spike. I would look next at sport-specific load, unusually long sessions, sleep/recovery context, or non-training stress rather than assuming total training load alone explains it."
    elif any(word in q for word in ("vertical", "elevation", "climb", "uphill", "downhill", "descent", "mountain")):
        mountain_sports = ["trail_running", "running", "hiking", "mountaineering"]
        gain = sum(float((context["sports_28d"].get(s) or {}).get("elevation_gain_m") or 0) for s in mountain_sports)
        loss = sum(float((context["sports_28d"].get(s) or {}).get("elevation_loss_m") or 0) for s in mountain_sports)
        downhill_load = sum(float((context["sports_28d"].get(s) or {}).get("descent_load") or 0) for s in mountain_sports)
        mechanical = sum(float((context["sports_28d"].get(s) or {}).get("mechanical_load") or 0) for s in mountain_sports)
        sessions = sum(int((context["sports_28d"].get(s) or {}).get("sessions") or 0) for s in mountain_sports)
        primary = context["objectives"][0] if context["objectives"] else None
        evidence.append({"metric": "vertical gain", "value": round(gain), "period": "last 28 days"})
        evidence.append({"metric": "vertical loss", "value": round(loss), "period": "last 28 days"})
        evidence.append({"metric": "downhill/eccentric load", "value": round(downhill_load, 1), "period": "last 28 days"})
        evidence.append({"metric": "mountain mechanical load", "value": round(mechanical, 1), "period": "last 28 days"})
        if primary and primary.get("elevation_m"):
            evidence.append({"metric": "next objective elevation", "value": primary["elevation_m"], "period": primary["date"]})
            answer = f"You accumulated about {gain:.0f} m of ascent and {loss:.0f} m of descent across {sessions} relevant sessions in the last 28 days. Downhill/eccentric load is {downhill_load:.0f} and total mountain mechanical load is {mechanical:.0f}. Your next objective, {primary['name']}, has about {primary['elevation_m']:.0f} m of climbing, so I would judge specificity from both ascent and descent exposure, especially in long sessions."
        else:
            answer = f"You accumulated about {gain:.0f} m of ascent and {loss:.0f} m of descent across {sessions} relevant sessions in the last 28 days. Downhill/eccentric load is {downhill_load:.0f}. Without an elevation-specific target objective, I can describe your mountain-load trend but not say whether it is sufficient for a particular race."
    elif any(word in q for word in ("compare", "improv", "progress", "last month", "four weeks")):
        recent = context.get("recent_four_weeks") or []
        prior = context.get("prior_four_weeks") or []
        recent_load = sum(float(w.get("load") or 0) for w in recent)
        prior_load = sum(float(w.get("load") or 0) for w in prior)
        recent_hours = sum(float(w.get("hours") or 0) for w in recent)
        prior_hours = sum(float(w.get("hours") or 0) for w in prior)
        change = _pct_change(recent_load, prior_load)
        evidence += [
            {"metric": "training load", "value": round(recent_load, 1), "period": "most recent 4 weeks"},
            {"metric": "training load", "value": round(prior_load, 1), "period": "previous 4 weeks"},
            {"metric": "training hours", "value": round(recent_hours, 1), "period": "most recent 4 weeks"},
            {"metric": "training hours", "value": round(prior_hours, 1), "period": "previous 4 weeks"},
        ]
        direction = "higher" if (change or 0) >= 0 else "lower"
        answer = f"Your most recent four weeks total {recent_load:.0f} load across {recent_hours:.1f} hours, compared with {prior_load:.0f} load across {prior_hours:.1f} hours in the previous four. That is {abs(change or 0):.0f}% {direction} by load. This describes training quantity; performance improvement still needs pace/power/HR efficiency or best-effort metrics."
    elif any(word in q for word in ("consistent", "consistency", "adherence", "missed")):
        adherence = context.get("adherence_28d_pct")
        weeks = context.get("recent_weeks") or []
        loads = [float(w.get("load") or 0) for w in weeks[-6:] if float(w.get("load") or 0) > 0]
        spread = (max(loads) - min(loads)) if len(loads) >= 2 else 0
        evidence.append({"metric": "plan adherence", "value": adherence, "period": "last 28 days"})
        evidence.append({"metric": "6-week load range", "value": round(spread, 1), "period": "recent completed weeks"})
        answer = f"Plan adherence over the last 28 days is {adherence:.0f}%" if adherence is not None else "There is not enough matched plan history yet to calculate adherence."
        if loads:
            answer += f" Recent weekly training load spans roughly {min(loads):.0f} to {max(loads):.0f}."
    else:
        objective = context["objectives"][0] if context["objectives"] else None
        evidence += [
            {"metric": "fitness", "value": state.get("fitness"), "period": context["as_of"]},
            {"metric": "fatigue", "value": state.get("fatigue"), "period": context["as_of"]},
            {"metric": "7-day load", "value": state.get("load_7d"), "period": "last 7 days"},
        ]
        target = f" Your next objective is {objective['name']} in {objective['days_away']} days." if objective else ""
        answer = f"Current fitness is {state.get('fitness', 0):.1f}, fatigue {state.get('fatigue', 0):.1f}, and form {state.get('form', 0):.1f}. Your last 7 days contain {state.get('load_7d', 0):.0f} load versus a typical recent week of about {state.get('typical_weekly_load_8w', 0):.0f}.{target} Ask about fatigue, vertical preparation, consistency, or a recent-vs-prior block comparison for a more specific analysis."

    return {"answer": answer, "evidence": evidence, "confidence": confidence, "facts_only": True}
