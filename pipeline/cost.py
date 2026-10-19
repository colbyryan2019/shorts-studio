"""cost.json: every paid call made for an episode (images, voice; video once it is built).

Each entry records the step, what was made, the model, the units billed and the amount in USD.
The amount is None when it isn't known in dollars (ElevenLabs bills characters against plan credits,
and an image model without known token rates reports tokens only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .episode import Episode
from .errors import PipelineError

COST_FILE = "cost.json"


def _path(ep: Episode):
    return ep.path / COST_FILE


def read_costs(ep: Episode) -> list[dict]:
    path = _path(ep)
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PipelineError(f"{path} is not valid JSON (line {e.lineno}): {e.msg}") from e
    if not isinstance(entries, list):
        raise PipelineError(f"{path} must be a list of cost entries")
    return entries


def log_cost(ep: Episode, step: str, item: str, model: str, units: float, unit: str,
             amount_usd: float | None) -> None:
    """Append one paid call. Written straight away, so an interrupted run still has its log."""
    entries = read_costs(ep)
    entries.append({
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "step": step,
        "item": item,
        "model": model,
        "units": units,
        "unit": unit,
        "amount_usd": None if amount_usd is None else round(amount_usd, 6),
    })
    ep.path.mkdir(parents=True, exist_ok=True)
    _path(ep).write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


@dataclass
class StepCost:
    step: str
    calls: int = 0
    usd: float = 0.0
    units: dict[str, float] = field(default_factory=dict)  # unit -> total, e.g. {"characters": 1240}
    unpriced: int = 0  # calls with no dollar amount


def summarize(entries: list[dict]) -> list[StepCost]:
    """Totals per step, in the order steps first appear."""
    steps: dict[str, StepCost] = {}
    for e in entries:
        s = steps.setdefault(e.get("step", "?"), StepCost(e.get("step", "?")))
        s.calls += 1
        if e.get("amount_usd") is None:
            s.unpriced += 1
        else:
            s.usd += e["amount_usd"]
        unit = e.get("unit")
        if unit:
            s.units[unit] = s.units.get(unit, 0) + (e.get("units") or 0)
    return list(steps.values())


def format_step(s: StepCost) -> str:
    units = ", ".join(f"{_num(v)} {k}" for k, v in s.units.items())
    money = f"${s.usd:.2f}"
    if s.unpriced:
        money += f" (+{s.unpriced} call{'s' if s.unpriced != 1 else ''} not priced in USD)"
    return f"{s.step}: {money} · {s.calls} call{'s' if s.calls != 1 else ''}" + (f" · {units}" if units else "")


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}"
