"""
Process-Bound Architecture - liveness detection with an automatic
healing trigger (experiment 016, availability test case 5).

Built on Gate 4 placement and experiment 010 self-healing in
replication.py, without modifying either: the watcher REUSES
ReplicatedPolicy.lose_holder() / needs_healing() / heal() and
placement's eligibility rules exactly as they are. Python standard
library only. No network, no database, no dependencies, and no
wall-clock time anywhere in this module.

WHAT THIS PROTOTYPE DEMONSTRATES (the case 5 outcomes)
------------------------------------------------------
1. Holders emit a heartbeat into a shared liveness view. Time is
   modeled deterministically: the view has an integer tick counter
   that only moves when advance() is called, and a holder beats by
   having beat() called for it at the current tick. Tests drive
   both explicitly, so no test sleeps and none is flaky.

2. A watcher distinguishes three states honestly, from missed
   beats alone (current tick minus the holder's last beat tick):
   - "alive": the holder beat at the current tick (0 missed);
   - "silent_short": 1..LOST_AFTER_MISSED_BEATS-1 missed beats.
     This is Gate 4's temporary-offline case in liveness form: the
     holder is STILL a holder, it still counts toward the floor,
     Gate 4 failover covers serving, and NO healing is triggered;
   - "lost": LOST_AFTER_MISSED_BEATS (3) or more missed beats. The
     holder is treated as permanently lost, in experiment 010's
     sense: lose_holder() drops its count.

3. The trigger is automatic: the same check() that declares a
   holder lost calls heal() with the watcher's candidate list in
   that same check. Nobody - no test, no operator - calls heal()
   or lose_holder() to make case 5's main outcome happen. The
   detection, the trigger, and the healing outcome are recorded:
   every check() returns a WatchReport and appends it to
   watcher.reports, and appends plain-dict events to watcher.events
   ("holder_lost", "healing_triggered", "below_floor",
   "holder_returned_as_candidate"), which the tests assert.

4. Negative controls, honestly: a holder silent briefly that beats
   again before the threshold was never declared lost, never
   healed over, and never lost holder status. When a lost holder
   has no eligible replacement, heal() reports below_floor (its
   own vocabulary, reused), and every later check() re-attempts
   with whatever candidates then exist and re-reports below_floor
   with a fresh report and log event - the loss is never dropped
   from the record because the first report was already sent.

5. Returning-holder rule (stated in case 5 before the build): a
   holder that was declared lost and later beats again rejoins as
   a CANDIDATE only. It is added to the watcher's candidate pool
   (opted in, as it was when it held), it is never re-added as a
   holder by the return itself, and the holder count never counts
   it twice. A later automatic heal can recruit it exactly like
   any other eligible candidate - and only then is it a holder
   again, with a fresh verified copy from heal(), not its old one.

HOW IT WORKS, IN PLAIN WORDS
----------------------------
LivenessView is the shared view: a tick counter and, per node id,
the tick of its last beat. It knows nothing about policies.
LivenessWatcher binds one ReplicatedPolicy, one view, and one
candidate list. tick(beating=[...]) is one modeled moment: advance
one tick, record a beat for each id named, then check(). check()
reads the states of the CURRENT holders; for each holder at or
past the lost threshold it records the detection, removes the
holder through lose_holder(), and - because the count is now
below the floor - healing triggers from that detection through
heal(). Recruited holders are registered in the view beating at
the current tick, so a recruit is alive from the moment it joins.
A check with no new loss but a still-open shortfall (an earlier
heal ran out of candidates) re-runs heal() so a candidate that
appears later is picked up without any new loss having to happen,
and so the below-floor state is re-reported every check.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- The heartbeat is a method call and the clock is a counter the
  caller advances. Nothing here detects a real machine dying, a
  process crashing, or a network partition; no real heartbeat is
  sent over any network, no timer fires on its own, and "automatic"
  means "triggered by check()/tick() without a separate heal call",
  not "runs unattended". A production watcher still needs a real
  clock, a real heartbeat transport, and a scheduler, none built.
- The threshold is a single fixed count of missed modeled beats
  (3), chosen for the prototype and stated wherever it is used.
  It is not tuned against any real heartbeat interval, jitter, or
  load, and a holder that is merely slow past the threshold is
  declared lost exactly like a dead one - the returning-holder
  rule is what keeps that mistake from double-counting it.
- Candidates are the list the watcher was constructed with, plus
  returned holders under the rule above. Nothing discovers
  candidates, and healing inherits experiment 010's limits whole:
  in-process PolicyNode objects on one machine over one shared
  snapshot file, "independent" and "eligible" mean distinct
  node_id and the operator's opt-in flag, recruits never reload a
  later revocation, and no copy moves over a network.
- One watcher watches one ReplicatedPolicy. There is no mesh-wide
  liveness view, no agreement between watchers, and no protection
  against two watchers healing the same policy twice.
- Deliberate removal by the owner (availability case 3) is NOT
  implemented or touched here.
- No performance, scale, or latency numbers were measured or are
  claimed.
"""

from __future__ import annotations

from dataclasses import dataclass

from replication import HealingReport, NodeSpec, ReplicatedPolicy

# The stated threshold: a holder silent for this many consecutive
# modeled beats is declared lost. 1..(this-1) missed beats is
# silent_short, the temporary case. Stated in case 5 and in every
# report that uses it.
LOST_AFTER_MISSED_BEATS = 3


class LivenessView:
    """The shared liveness view: one tick counter, last-beat ticks.

    Deterministic by construction: the tick only moves when
    advance() is called, and a beat is recorded only when beat()
    is called. Registering a node records its first beat at the
    current tick, so a newly known holder starts alive.
    """

    def __init__(self, lost_after_missed: int = LOST_AFTER_MISSED_BEATS):
        if lost_after_missed < 1:
            raise ValueError("lost threshold must be at least 1 missed beat")
        self.lost_after_missed = lost_after_missed
        self.current_tick = 0
        self._last_beat: dict[str, int] = {}

    def register(self, node_id: str) -> None:
        self._last_beat[node_id] = self.current_tick

    def beat(self, node_id: str) -> None:
        self._last_beat[node_id] = self.current_tick

    def advance(self, ticks: int = 1) -> int:
        if ticks < 0:
            raise ValueError("the modeled clock does not run backwards")
        self.current_tick += ticks
        return self.current_tick

    def missed_beats(self, node_id: str) -> int:
        if node_id not in self._last_beat:
            raise KeyError(f"no liveness record for node: {node_id}")
        return self.current_tick - self._last_beat[node_id]

    def state(self, node_id: str) -> str:
        """ "alive" | "silent_short" | "lost", from missed beats. """
        missed = self.missed_beats(node_id)
        if missed == 0:
            return "alive"
        if missed < self.lost_after_missed:
            return "silent_short"
        return "lost"

    def knows(self, node_id: str) -> bool:
        return node_id in self._last_beat


@dataclass(frozen=True)
class WatchReport:
    """What one check() saw and did, stated honestly.

    status vocabulary deliberately extends experiment 010's:
    "healed" only when an automatic heal in this check restored
    the floor, "below_floor" whenever the check ends below the
    floor (lost with no eligible replacement, or a still-open
    shortfall being re-reported), "silent_short" when no healing
    was needed or triggered but at least one holder is in the
    temporary silent state, and "no_action" when every holder is
    alive and the floor is met. healing is the HealingReport of
    the heal this check triggered, or None when none was
    triggered (no loss, no open shortfall).
    """

    policy_hash: str
    tick: int
    holder_states: dict[str, str]
    silent_short_node_ids: tuple[str, ...]
    lost_node_ids: tuple[str, ...]
    returned_candidate_node_ids: tuple[str, ...]
    healing: HealingReport | None
    holder_count_before: int
    holder_count_after: int
    floor: int
    floor_met: bool
    status: str  # "healed" | "below_floor" | "silent_short" | "no_action"

    def to_dict(self) -> dict:
        return {
            "policy_hash": self.policy_hash,
            "tick": self.tick,
            "holder_states": dict(self.holder_states),
            "silent_short_node_ids": list(self.silent_short_node_ids),
            "lost_node_ids": list(self.lost_node_ids),
            "returned_candidate_node_ids": list(
                self.returned_candidate_node_ids
            ),
            "healing": self.healing.to_dict() if self.healing else None,
            "holder_count_before": self.holder_count_before,
            "holder_count_after": self.holder_count_after,
            "floor": self.floor,
            "floor_met": self.floor_met,
            "status": self.status,
        }


class LivenessWatcher:
    """Watches one ReplicatedPolicy's holders and heals losses.

    Construct with the placed policy and the candidate list an
    automatic heal may recruit from (placement's eligibility rules
    apply inside heal(), unchanged). Current holders are registered
    in the view beating at tick 0.
    """

    def __init__(
        self,
        replicated: ReplicatedPolicy,
        *,
        candidates: list[NodeSpec],
        lost_after_missed: int = LOST_AFTER_MISSED_BEATS,
    ):
        self.replicated = replicated
        self.view = LivenessView(lost_after_missed)
        self._candidates: list[NodeSpec] = list(candidates)
        self._declared_lost: set[str] = set()
        self._returned_candidates: set[str] = set()
        self.reports: list[WatchReport] = []
        self.events: list[dict] = []
        for node_id in replicated.holder_node_ids():
            self.view.register(node_id)

    # -- inputs -----------------------------------------------------------

    def beat(self, node_id: str) -> None:
        """Record one heartbeat for node_id at the current tick.

        A beat from a holder previously declared lost is the
        returning-holder case: the node is recorded beating and
        joins the candidate pool (see the module docstring rule);
        it is NOT re-added as a holder here.
        """
        self.view.beat(node_id)
        if node_id in self._declared_lost:
            self._note_returned(node_id)

    def tick(self, *, beating: list[str] | tuple[str, ...] = ()) -> WatchReport:
        """One modeled moment: advance one tick, beat, then check."""
        self.view.advance(1)
        for node_id in beating:
            self.beat(node_id)
        return self.check()

    # -- candidate pool -----------------------------------------------------

    def candidate_node_ids(self) -> tuple[str, ...]:
        return tuple(spec.node_id for spec in self._candidates)

    def _note_returned(self, node_id: str) -> None:
        if node_id in self.replicated.holder_node_ids():
            return  # already recruited back by a heal; it is a holder
        if node_id in self._returned_candidates:
            return
        self._returned_candidates.add(node_id)
        if node_id not in self.candidate_node_ids():
            # It held before, so it was opted in; candidacy restores
            # exactly that eligibility, nothing more.
            self._candidates.append(NodeSpec(node_id, serve_policies=True))
        self.events.append(
            {
                "event": "holder_returned_as_candidate",
                "tick": self.view.current_tick,
                "node_id": node_id,
            }
        )

    # -- the watch ----------------------------------------------------------

    def check(self) -> WatchReport:
        """Classify current holders; heal automatically on a loss.

        A holder at or past the lost threshold is declared lost
        (detection recorded), removed through lose_holder(), and
        heal() is triggered from that detection in this same check.
        A check that finds no new loss but a still-open shortfall
        re-runs heal() against the current candidate pool and
        re-reports below_floor, so an unhealed loss keeps being
        reported instead of going silent.
        """
        holders_before = self.replicated.holder_node_ids()
        count_before = len(holders_before)
        states = {}
        for node_id in holders_before:
            if not self.view.knows(node_id):
                self.view.register(node_id)
            states[node_id] = self.view.state(node_id)
        silent_short = tuple(
            n for n in holders_before if states[n] == "silent_short"
        )
        newly_lost = tuple(n for n in holders_before if states[n] == "lost")

        returned_now = tuple(
            sorted(self._returned_candidates)
        )

        healing: HealingReport | None = None
        for node_id in newly_lost:
            self._declared_lost.add(node_id)
            self.events.append(
                {
                    "event": "holder_lost",
                    "tick": self.view.current_tick,
                    "node_id": node_id,
                    "missed_beats": self.view.missed_beats(node_id),
                    "threshold": self.view.lost_after_missed,
                }
            )
            self.replicated.lose_holder(node_id)

        if newly_lost or self.replicated.needs_healing():
            healing = self.replicated.heal(list(self._candidates))
            for node_id in healing.recruited_node_ids:
                self.view.register(node_id)
                self._returned_candidates.discard(node_id)
            self.events.append(
                {
                    "event": "healing_triggered",
                    "tick": self.view.current_tick,
                    "triggered_by": list(newly_lost),
                    "recruited_node_ids": list(healing.recruited_node_ids),
                    "holder_count_before": healing.holder_count_before,
                    "holder_count_after": healing.holder_count_after,
                    "floor_restored": healing.floor_restored,
                    "status": healing.status,
                }
            )
            if not healing.floor_restored:
                self.events.append(
                    {
                        "event": "below_floor",
                        "tick": self.view.current_tick,
                        "holder_count": healing.holder_count_after,
                        "floor": healing.floor,
                        "shortfall": self.replicated.shortfall(),
                    }
                )

        count_after = len(self.replicated.holder_node_ids())
        floor_met = count_after >= self.replicated.floor
        if healing is not None and healing.floor_restored and newly_lost:
            status = "healed"
        elif not floor_met:
            status = "below_floor"
        elif healing is not None and healing.floor_restored:
            # Shortfall closed by a re-check with no new loss.
            status = "healed"
        elif silent_short:
            status = "silent_short"
        else:
            status = "no_action"

        report = WatchReport(
            policy_hash=self.replicated.policy_hash,
            tick=self.view.current_tick,
            holder_states=states,
            silent_short_node_ids=silent_short,
            lost_node_ids=newly_lost,
            returned_candidate_node_ids=tuple(
                n
                for n in returned_now
                if n not in self.replicated.holder_node_ids()
            ),
            healing=healing,
            holder_count_before=count_before,
            holder_count_after=count_after,
            floor=self.replicated.floor,
            floor_met=floor_met,
            status=status,
        )
        self.reports.append(report)
        return report
