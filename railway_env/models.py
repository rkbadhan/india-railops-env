"""Serializable problem and decision formats. All times are integer minutes."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class Track:
    id: str
    length_m: int
    kind: str = "loop"

    def __post_init__(self):
        if not self.id or self.kind not in ("main", "loop"):
            raise ValueError("a track needs an id and kind main/loop")
        integer(self.length_m, "track length", 1)


@dataclass(frozen=True)
class Station:
    code: str
    name: str
    tracks: tuple[Track, ...]

    def __post_init__(self):
        if not self.code or not self.tracks or len({t.id for t in self.tracks}) != len(self.tracks):
            raise ValueError("station codes and track ids must be nonempty and unique")


@dataclass(frozen=True)
class Block:
    a: str
    b: str
    up_minutes: int
    down_minutes: int
    distance_km: float = 0.0

    def __post_init__(self):
        if not self.a or not self.b or self.a == self.b:
            raise ValueError("a block must join different stations")
        integer(self.up_minutes, "UP running time", 1)
        integer(self.down_minutes, "DN running time", 1)

    @property
    def key(self):
        return tuple(sorted((self.a, self.b)))

    def running_time(self, origin, destination):
        if (origin, destination) == (self.a, self.b):
            return self.up_minutes
        if (origin, destination) == (self.b, self.a):
            return self.down_minutes
        raise ValueError("stations do not match this block")


@dataclass(frozen=True)
class Corridor:
    section: str
    stations: tuple[Station, ...]
    blocks: tuple[Block, ...]
    source_line_type: str = "unknown"
    operating_mode: str = "one shared block in both directions"
    block_mode: str = "shared"

    def __post_init__(self):
        if self.block_mode not in ("shared", "directional"):
            raise ValueError("block_mode must be shared or directional")
        object.__setattr__(self, "operating_mode", "separate block per direction" if self.block_mode == "directional"
                           else "one shared block in both directions")
        codes = [s.code for s in self.stations]
        if len(codes) < 2 or len(set(codes)) != len(codes):
            raise ValueError("a corridor needs at least two distinct stations")
        expected = {tuple(sorted(pair)) for pair in zip(codes, codes[1:])}
        if len(self.blocks) != len(expected) or {b.key for b in self.blocks} != expected:
            raise ValueError("blocks must connect exactly the adjacent corridor stations")

    def station(self, code):
        return next(s for s in self.stations if s.code == code)

    def block(self, a, b):
        key = tuple(sorted((a, b)))
        return next(block for block in self.blocks if block.key == key)

    def block_resource(self, origin, destination):
        pair = (origin, destination) if self.block_mode == "directional" else tuple(sorted((origin, destination)))
        return ("block", *pair)

    def closure_resources(self, block):
        a, b = block
        return tuple(dict.fromkeys((self.block_resource(a, b), self.block_resource(b, a))))


@dataclass(frozen=True)
class Train:
    id: str
    kind: str
    route: tuple[str, ...]
    length_m: int
    weight: int
    release: int
    scheduled_arrivals: tuple[int, ...]
    scheduled_departures: tuple[int, ...]
    running_times: tuple[int, ...]
    dwell_times: tuple[int, ...]
    source_train_no: str = ""

    def __post_init__(self):
        if not self.id or self.kind not in ("passenger", "freight"):
            raise ValueError("a train needs an id and kind passenger/freight")
        n = len(self.route)
        if n < 2 or len(set(self.route)) != n:
            raise ValueError("a train route needs at least two distinct stations")
        if any(len(x) != n for x in (self.scheduled_arrivals, self.scheduled_departures, self.dwell_times)):
            raise ValueError("train time arrays must match the route")
        if len(self.running_times) != n - 1:
            raise ValueError("one running time is required for each leg")
        for name, value in (("length", self.length_m), ("weight", self.weight)):
            integer(value, name, 1)
        integer(self.release, "release")
        for times in (self.scheduled_arrivals, self.scheduled_departures, self.dwell_times):
            for value in times:
                integer(value, "train time")
        for value in self.running_times:
            integer(value, "train running time", 1)
        if self.release < self.scheduled_arrivals[0] or self.dwell_times[-1] != 0:
            raise ValueError("release cannot precede the timetable; terminal dwell must be zero")
        for i in range(n):
            if self.scheduled_departures[i] < self.scheduled_arrivals[i] + self.dwell_times[i]:
                raise ValueError("scheduled departure precedes minimum dwell")
            if i and self.scheduled_arrivals[i] < self.scheduled_departures[i - 1] + self.running_times[i - 1]:
                raise ValueError("the nominal timetable must allow the given running times")


@dataclass(frozen=True)
class Disruption:
    kind: str
    train_id: str = ""
    delay: int = 0
    block: tuple[str, ...] = ()
    start: int = 0
    end: int = 0


@dataclass(frozen=True)
class Scenario:
    id: str
    seed: int
    corridor: Corridor
    trains: tuple[Train, ...]
    disruption: Disruption
    window_start: int = 0  # Minutes since midnight in the source timetable.
    duration: int = 120
    clearance: int = 1
    metadata: dict = field(default_factory=dict)
    additional_disruptions: tuple[Disruption, ...] = ()

    def __post_init__(self):
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        integer(self.duration, "duration", 1)
        integer(self.clearance, "clearance", 1)
        integer(self.window_start, "window start")
        if not self.trains or len({t.id for t in self.trains}) != len(self.trains):
            raise ValueError("scenario train ids must be nonempty and unique")
        indices = {s.code: i for i, s in enumerate(self.corridor.stations)}
        for train in self.trains:
            if any(code not in indices for code in train.route):
                raise ValueError(f"{train.id}: unknown station")
            steps = [indices[b] - indices[a] for a, b in zip(train.route, train.route[1:])]
            if not (all(d == 1 for d in steps) or all(d == -1 for d in steps)):
                raise ValueError(f"{train.id}: route must follow adjacent stations in one direction")
            for code in train.route:
                if not any(t.length_m >= train.length_m for t in self.corridor.station(code).tracks):
                    raise ValueError(f"{train.id} does not fit any track at {code}")
        late_delays = {}
        for event in self.disruptions:
            self._validate_event(event)
            if event.kind == "late_train":
                late_delays[event.train_id] = late_delays.get(event.train_id, 0) + event.delay
        for train_id, delay in late_delays.items():
            train = next(t for t in self.trains if t.id == train_id)
            if train.release != train.scheduled_arrivals[0] + delay:
                raise ValueError("late-train release must include the disruption exactly once")

    def _validate_event(self, event):
        if event.kind == "late_train":
            if event.train_id not in {t.id for t in self.trains}:
                raise ValueError("late-train disruption references an unknown train")
            integer(event.delay, "late-train delay", 1)
        elif event.kind == "signal_failure":
            if len(event.block) != 2 or tuple(sorted(event.block)) not in {b.key for b in self.corridor.blocks}:
                raise ValueError("signal failure references an unknown block")
            integer(event.start, "closure start")
            integer(event.end, "closure end", event.start + 1)
        elif event.kind != "none":
            raise ValueError("unknown disruption kind")

    @property
    def disruptions(self):
        return (self.disruption,) + self.additional_disruptions

    def closures(self):
        """Merged closures per resource. A signal failure closes both directions."""
        resources = {}
        for event in self.disruptions:
            if event.kind == "signal_failure":
                for key in self.corridor.closure_resources(event.block):
                    resources.setdefault(key, []).append((event.start, event.end))
        for key, intervals in resources.items():
            merged = []
            for start, end in sorted(intervals):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            for start, end in merged:
                yield key, start, end

    @property
    def horizon(self):
        # Serially clearing all trains is a feasible upper bound with external entry queues.
        last = max([self.duration] + [event.end for event in self.disruptions] +
                   [max(t.release, t.scheduled_departures[-1]) for t in self.trains])
        return last + sum(sum(t.running_times) + sum(t.dwell_times) +
                          len(t.route) * self.clearance for t in self.trains) + 30

    def to_dict(self):
        return {"schema_version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        if data.pop("schema_version", None) != 1:
            raise ValueError("unsupported scenario schema_version")
        corridor = data.pop("corridor")
        stations = tuple(Station(s["code"], s["name"], tuple(Track(**t) for t in s["tracks"]))
                         for s in corridor["stations"])
        data["corridor"] = Corridor(**{**corridor, "stations": stations,
                                       "blocks": tuple(Block(**b) for b in corridor["blocks"])})
        trains = []
        for train in data.pop("trains"):
            train = dict(train)
            for key in ("route", "scheduled_arrivals", "scheduled_departures", "running_times", "dwell_times"):
                train[key] = tuple(train[key])
            trains.append(Train(**train))
        data["trains"] = tuple(trains)
        data["disruption"] = Disruption(**{**data["disruption"], "block": tuple(data["disruption"].get("block", ()))})
        data["additional_disruptions"] = tuple(Disruption(**{**e, "block": tuple(e.get("block", ()))})
                                                for e in data.get("additional_disruptions", ()))
        return cls(**data)


@dataclass(frozen=True)
class Visit:
    train_id: str
    station: str
    arrival: int
    departure: int
    track: str


@dataclass(frozen=True)
class Plan:
    visits: tuple[Visit, ...]

    def to_dict(self):
        return {"visits": [asdict(v) for v in self.visits]}

    @classmethod
    def from_dict(cls, data):
        if set(data) != {"visits"} or not isinstance(data["visits"], list):
            raise ValueError("a complete plan must contain a visits list")
        return cls(tuple(Visit(**v) for v in data["visits"]))


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_scenario(path):
    return Scenario.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
