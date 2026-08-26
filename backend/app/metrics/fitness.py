from datetime import date, timedelta
from typing import Iterable


def ewma_series(daily_load: dict[date, float], start: date, end: date, fitness_tau: float = 42.0, fatigue_tau: float = 7.0):
    fitness = 0.0
    fatigue = 0.0
    result = []
    d = start
    while d <= end:
        load = float(daily_load.get(d, 0.0))
        fitness += (load - fitness) / fitness_tau
        fatigue += (load - fatigue) / fatigue_tau
        result.append({"date": d.isoformat(), "load": round(load, 2), "fitness": round(fitness, 2), "fatigue": round(fatigue, 2), "form": round(fitness - fatigue, 2)})
        d += timedelta(days=1)
    return result
