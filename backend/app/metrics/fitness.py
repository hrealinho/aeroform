from datetime import date, timedelta

from app.core.config import settings

# One year of prior load is enough for a 42-day fitness EWMA to converge, so a
# narrowed chart window starts from the athlete's real state instead of from zero.
DEFAULT_WARMUP_DAYS = 365


def ewma_series(
    daily_load: dict[date, float],
    start: date,
    end: date,
    fitness_tau: float | None = None,
    fatigue_tau: float | None = None,
    warmup_days: int = 0,
):
    """Fitness/fatigue/form series over [start, end].

    ``warmup_days`` runs the EWMA forward over load before ``start`` without emitting
    rows. Without it, requesting a 30-day window showed fitness ramping up from zero
    rather than the athlete's actual accumulated fitness, which made every absolute
    value on a zoomed chart wrong.

    Time constants default to the configured values so FITNESS_TAU_DAYS /
    FATIGUE_TAU_DAYS actually take effect.
    """
    fitness_tau = float(fitness_tau if fitness_tau is not None else settings.fitness_tau_days)
    fatigue_tau = float(fatigue_tau if fatigue_tau is not None else settings.fatigue_tau_days)
    fitness = 0.0
    fatigue = 0.0
    result = []

    d = start - timedelta(days=max(0, int(warmup_days)))
    while d <= end:
        load = float(daily_load.get(d, 0.0))
        fitness += (load - fitness) / fitness_tau
        fatigue += (load - fatigue) / fatigue_tau
        if d >= start:
            result.append({"date": d.isoformat(), "load": round(load, 2), "fitness": round(fitness, 2), "fatigue": round(fatigue, 2), "form": round(fitness - fatigue, 2)})
        d += timedelta(days=1)
    return result
