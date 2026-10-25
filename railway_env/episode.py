"""Text observations and isolated try_plan / submit actions for a controller agent."""

from copy import deepcopy

from .baselines import dispatch
from .models import Plan
from .simulator import RailwaySimulator
from .scoring import normalized_score
from .references import validate_reference


# Bump when anything a controller sees or is scored on changes; recorded in every result file.
ENV_VERSION = "dispatch_env_v3_policy_required"

TOOLS = [
    {"name": "try_plan", "description": "Dry-run a dispatch proposal without changing the episode. Always give "
                                        "policy; priority_order and holds adjust the plan it starts from.",
     "input_schema": {"type": "object", "properties": {
         "holds": {"description": "Optional. Keep a train at a non-terminal station until the given minute.",
                   "type": "array", "items": {"type": "object", "properties": {
             "train_id": {"type": "string"}, "station": {"type": "string"},
             "until": {"type": "integer", "minimum": 0}},
             "required": ["train_id", "station", "until"], "additionalProperties": False}},
         "priority_order": {"description": "Optional. Train ids planned ahead of everything else, first to last.",
                            "type": "array", "items": {"type": "string"}},
         "policy": {"description": "Required. The plan to start from: passenger_first is the default plan "
                                   "(passenger trains first, then heavier trains first); controller_rule plans "
                                   "trains in the order they become ready.",
                    "type": "string", "enum": ["controller_rule", "passenger_first"]},
         "visits": {"type": "array", "items": {"type": "object", "properties": {
             "train_id": {"type": "string"}, "station": {"type": "string"},
             "arrival": {"type": "integer", "minimum": 0},
             "departure": {"type": "integer", "minimum": 0}, "track": {"type": "string"}},
             "required": ["train_id", "station", "arrival", "departure", "track"],
             "additionalProperties": False}}}, "additionalProperties": False}},
]
TOOLS.append({**TOOLS[0], "name": "submit", "description": "Submit the final proposal and end the episode. Same arguments as try_plan; policy is required, "
                                           "and priority_order and holds adjust the plan it starts from."})


def tools_for(allow_timetable):
    tools = [deepcopy(tool) for tool in TOOLS]
    if not allow_timetable:
        for tool in tools:
            del tool["input_schema"]["properties"]["visits"]
            tool["input_schema"]["required"] = ["policy"]
    return tools


def decisions_text(scenario, plan):
    trains = {t.id: t for t in scenario.trains}
    lines = []
    for visit in sorted(plan.visits, key=lambda v: (v.departure, v.train_id, v.station)):
        train = trains[visit.train_id]
        if visit.station == train.route[-1]:
            continue
        action = "waits" if visit.departure > visit.arrival else "passes"
        lines.append(f"{visit.train_id} {action} at {visit.station} on {visit.track}; "
                     f"arrives minute {visit.arrival}, departs minute {visit.departure}.")
    return lines


class DispatchEpisode:
    def __init__(self, scenario, time_limit=10.0, reference=None, trial_budget=None, allow_timetable=True):
        if not 0 < time_limit <= 3600:
            raise ValueError("time_limit must be in (0, 3600]")
        self.scenario = scenario
        self.submitted = False
        self.final_result = None
        self.time_limit = time_limit
        if trial_budget is not None and (type(trial_budget) is not int or trial_budget < 0):
            raise ValueError("trial_budget must be a nonnegative integer")
        self.reference = validate_reference(scenario, reference) if reference is not None else None
        self.trial_budget = trial_budget
        self.trials_used = 0
        self.allow_timetable = allow_timetable
        self._default_score = False  # False = not computed yet; None = unavailable

    def default_plan_score(self):
        """Weighted delay of the passenger-first plan, computed from the scenario alone.

        It never reads the stored reference, so it cannot reveal the optimum or any stored plan.
        """
        if self._default_score is False:
            try:
                plan = dispatch(self.scenario, "passenger_first")
                self._default_score = RailwaySimulator(self.scenario).evaluate(plan)["weighted_delay"]
            except (ValueError, RuntimeError):
                self._default_score = None
        return self._default_score

    def observe(self):
        s = self.scenario
        start = f"{s.window_start // 60:02d}:{s.window_start % 60:02d}"
        lines = [f"DISPATCH {s.id}", f"Window: {start} + {s.duration} minutes; all action times are window-relative.",
                 f"Track: {' - '.join(st.code for st in s.corridor.stations)} ({s.corridor.section}).",
                 f"Model: {s.corridor.operating_mode}; source line type: {s.corridor.source_line_type}.",
                 f"Each block and berth remains reserved {s.clearance} minute(s) after arrival/departure.",
                 "No train starts in transit. Ready trains can queue outside their entry station.",
                 "No early departures. A train must fit its assigned main or loop.",
                 "Score: sum(weight * positive final-arrival delay); lower is better. Finish EVERY train."]
        for event in s.disruptions:
            if event.kind == "late_train":
                lines.append(f"Problem: {event.train_id} is {event.delay} minutes late entering the stretch.")
            elif event.kind == "signal_failure":
                lines.append(f"Problem: {'-'.join(event.block)} CLOSED during [{event.start}, {event.end}).")
        for station in s.corridor.stations:
            lines.append(f"{station.code} ({station.name}): " + ", ".join(
                f"{t.id} {t.length_m}m" for t in station.tracks))
        for block in s.corridor.blocks:
            lines.append(f"Block {block.a}-{block.b}: reference {block.up_minutes}/{block.down_minutes} min UP/DN.")
        for train in s.trains:
            late = train.release - train.scheduled_arrivals[0]
            location = "waiting outside" if train.release == 0 else "expected at"
            lines.append(f"{train.id} [{train.kind}, {train.length_m}m, weight {train.weight}]: "
                         f"{location} {train.route[0]}, ready {train.release} (+{late} late); "
                         f"due {train.route[-1]} at {train.scheduled_arrivals[-1]}.")
            lines.append("  timetable (arrival/departure): " + ", ".join(
                f"{code} {a}/{d}" for code, a, d in
                zip(train.route, train.scheduled_arrivals, train.scheduled_departures)))
            lines.append("  running minutes: " + ", ".join(map(str, train.running_times)))
        default = self.default_plan_score()
        if default is not None:
            lines.append(f"Default plan (passenger first) scores {default}. Submitting it is always allowed.")
        lines.append("Use try_plan with holds and/or a priority_order; inspect the compiled decisions, then submit.")
        return "\n".join(lines)

    def _evaluate(self, proposal):
        if not isinstance(proposal, dict):
            return {"valid": False, "errors": ["proposal must be an object"], "weighted_delay": None, "reward": None}
        try:
            if "visits" in proposal:
                if not self.allow_timetable:
                    raise ValueError("full timetables are disabled; use holds and priority_order")
                plan = Plan.from_dict(proposal)
            else:
                if set(proposal) - {"holds", "priority_order", "policy"}:
                    raise ValueError("unknown proposal fields")
                if "policy" not in proposal:
                    raise ValueError("policy is required: use passenger_first (the default plan) or controller_rule; "
                                     "priority_order and holds then adjust it")
                plan = dispatch(self.scenario, proposal["policy"],
                                proposal.get("holds", ()), proposal.get("priority_order", ()))
            simulator = RailwaySimulator(self.scenario)
            result = simulator.evaluate(plan)
            if result["valid"]:
                result.update(plan=plan.to_dict(), decisions=decisions_text(self.scenario, plan),
                              events=simulator.events(plan))
            return result
        except (ValueError, TypeError, KeyError, StopIteration, AttributeError) as error:
            return {"valid": False, "errors": [str(error)], "weighted_delay": None, "reward": None}
        except RuntimeError as error:
            return {"valid": False, "errors": [f"internal dispatch error: {error}"],
                    "error_type": "internal_error", "weighted_delay": None, "reward": None}

    def try_plan(self, proposal):
        if self.submitted:
            raise ValueError("episode already submitted")
        if self.trial_budget is not None and self.trials_used >= self.trial_budget:
            return {"valid": False, "error_type": "trial_budget_exhausted", "errors": ["submit your final plan"],
                    "weighted_delay": None, "reward": None}
        self.trials_used += 1
        return self._evaluate(proposal)

    def submit(self, proposal):
        if self.submitted:
            raise ValueError("episode already submitted")
        self.final_result = self._evaluate(proposal)
        if not self.final_result["valid"]:
            # A terminal penalty below every feasible score prevents unsafe or
            # incomplete submissions from escaping delay costs in an RL loop.
            self.final_result["reward"] = -(self.scenario.horizon * sum(t.weight for t in self.scenario.trains) + 1)
        self.submitted = True
        self.final_result.update(excess_delay_over_optimum=None, improvement_share=None,
                                 normalized_reward=None, reward_scale=None,
                                 normalization_status="reference_unavailable" if self.final_result["valid"] else "invalid_plan")
        # Reference evaluation cannot undo a submission or blame an agent for
        # an environment failure. Never import or call the solver on this path.
        if self.reference is not None:
            try:
                oracle = self.reference["oracle"]
                baseline = self.reference["passenger_first"]["weighted_delay"]
                self.final_result.update(normalized_score(self.final_result["weighted_delay"], baseline, oracle))
                self.final_result["reference"] = {"passenger_first": baseline,
                    "best_score": oracle["weighted_delay"] if oracle["proven_optimal"] else None,
                    "incumbent": oracle["weighted_delay"], "oracle_status": oracle["status"], "best_bound": oracle["best_bound"]}
            except (RuntimeError, ImportError, ValueError, KeyError, TypeError) as error:
                self.final_result.update(normalization_status="evaluation_error",
                                         evaluation_error={"type": type(error).__name__, "message": str(error)})
        return {**self.final_result, "done": True}

    def call(self, name, arguments=None):
        if name == "observe":
            return {"observation": self.observe(), "done": self.submitted}
        if name == "try_plan":
            return self.try_plan({} if arguments is None else arguments)
        if name == "submit":
            return self.submit({} if arguments is None else arguments)
        raise ValueError("unknown tool; use observe, try_plan, or submit")
