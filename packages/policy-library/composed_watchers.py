"""
Process-Bound Architecture - composed liveness and snapshot
delivery watchers over one placed policy (experiment 023,
availability test case 8).

Experiments 016 and 022 built two watchers over a placed
policy, each in the same style (one watcher over one
ReplicatedPolicy, a deterministic modeled clock, a report per
check, a plain-dict event log): liveness.py's LivenessWatcher,
which declares a holder lost at 3 missed beats and triggers
heal() from that declaration, and snapshot_delivery.py's
SnapshotDeliveryWatcher, which notices the placement's shared
snapshot file changed and calls reload_snapshot() itself.
Each was proven against the placement separately; 022's
heal-after-delivery proof used explicit lose_holder()/heal()
calls, not 016's automatic trigger, so the two were never run
composed over the same placement. This module composes them:
it REUSES both watchers exactly as they stand (neither module
is modified, and no mechanism is duplicated here) and adds
only the coordination the composition needs. Python standard
library only. No network, no database, no dependencies, and
no wall-clock time anywhere in this module.

WHAT THIS PROTOTYPE DEMONSTRATES (the case 8 outcomes)
------------------------------------------------------
1. One composed cycle, one stated order. ComposedWatcher.tick()
   is one composed check cycle: it drives the delivery watcher's
   tick FIRST and the liveness watcher's tick SECOND, and
   records both sub-reports in one ComposedReport (with one
   composed_cycle event) so what happened in a cycle is named
   in one place, in the plain report style of 016/022. The
   order is delivery before liveness, for one concrete reason
   the hard case proves: a heal recruits a replacement by
   constructing a PolicyNode over the placement's registry
   snapshot, so if a delivery and a loss land in the same
   window, delivering first means the automatic heal that
   follows recruits over the UPDATED record - the recruit
   refuses a policy revoked in that same cycle - while healing
   first would recruit over the record the pending delivery was
   about to replace (case 6's stale-recruit window, reopened by
   ordering alone).

2. The hard case in one window. A revocation delivered over
   the snapshot file (anchor lifecycle followed, as in cases
   6/7) and a holder crossing the lost threshold in the same
   cycle window produce, in that one composed cycle: the
   delivery applied, the holder declared lost, and the
   automatic heal recruiting over the updated record. A
   non-revoked policy placed over the same snapshot keeps
   serving after its own delivery, exactly as in case 7.

3. No double action. A composed cycle over an unchanged
   snapshot with every holder beating does exactly nothing:
   the delivery sub-report is no_change (no reload, holder
   views not rebuilt), the liveness sub-report is no_action
   (no loss, no heal), and the holder count is stable. A cycle
   with only a delivery delivers and does not heal; a cycle
   with only a loss heals and does not deliver. Each watcher
   keeps its own no-work discipline; the coordinator adds no
   work of its own on either path.

4. Refusal composition. A delivery can be refused (the
   snapshot file on disk then holds a record the reload
   rejected: honestly grown but anchor_stale, or tampered).
   That refusal must not block the same cycle's healing - but
   heal() recruits by constructing a PolicyNode over the
   placement's snapshot PATH, which at that moment names the
   refused file, so a naive heal would either fail on the
   refused record or, worse, recruit over it. The coordinator
   therefore keeps the bytes of the last record the placement
   actually loaded (recorded at construction, moved only when
   a delivery STANDS). In a cycle whose delivery is refused,
   it writes those last-good bytes to a sidecar file next to
   the snapshot, points the placement's heal path at the
   sidecar for the duration of that cycle's liveness tick,
   and restores the placement's snapshot path to the delivery
   snapshot before the cycle returns - so a heal in a refused
   cycle recruits a real, verified holder over the last good
   record, the refusal stays pending and keeps being reported
   by the delivery watcher (whose own path never moves), and
   nothing about the heal forces the refused delivery through:
   when the refusal's cause is later fixed, a normal composed
   cycle delivers to every holder, the sidecar-recruited one
   included, through the delivery watcher's usual reload.

HOW IT WORKS, IN PLAIN WORDS
----------------------------
ComposedWatcher binds one ReplicatedPolicy, constructs its
SnapshotDeliveryWatcher and its LivenessWatcher over that same
placement (the liveness candidate list is the coordinator's
one construction input beyond the placement), and starts both
from the placement's current state, exactly as each watcher
does on its own. tick(beating=[...]) advances the composed
counter by one and runs the two watchers' own ticks in the
stated order, with the refused-cycle sidecar swap described
above as the only interposition, and it is undone before the
tick returns (in a finally block, so a liveness exception
cannot leave the placement pointing at the sidecar). Every
tick returns a ComposedReport and appends it to
watcher.reports, and appends one plain-dict composed_cycle
event to watcher.events; the two watchers' own reports and
event logs remain theirs and are asserted alongside.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- "Automatic" and "composed" mean precisely "driven by
  tick() in the stated order without a separate reload or
  heal call". No timer, scheduler, thread, or file watcher
  fires tick() unattended, and the modeled clocks are the
  counters the two watchers already keep, advanced by the
  coordinator's ticks. A placement whose coordinator is never
  ticked is exactly as stale and exactly as unhealed as one
  with no watchers at all.
- One coordinator composes the two watchers over ONE placed
  policy, in-process, on one machine, over one shared
  snapshot file. Two placed policies over the same snapshot
  each need their own coordinator (or delivery watcher, as
  the tests run for the non-revoked policy), and nothing
  coordinates two coordinators: two coordinators over the
  SAME placement could still double-act, exactly as 016 and
  022 each stated for two watchers.
- The last-good record is kept as bytes in the coordinator's
  memory, recorded at construction and moved only on a
  delivery that stands; the sidecar file
  (<snapshot>.last-good-<policy prefix>) is written next to
  the snapshot only in refused cycles and is left in place
  afterwards, because a holder recruited from it names it as
  its snapshot path until a later delivered reload moves it.
  Nothing cleans the sidecar up, encrypts it, or protects it
  from another writer, and a coordinator constructed over a
  placement whose snapshot file was already changed (but
  never loaded) takes that file as last good, the same
  construction-time baseline honesty 022 stated.
- The coordinator interposes on the placement's snapshot
  path only around a refused cycle's liveness tick, and only
  for heal()'s benefit; it never edits the delivery
  snapshot, never re-anchors, and never repairs a refusal -
  a refusal's cause is fixed outside, as in 022.
- Inherited limits stand unchanged: in-process holders on
  one machine, HMAC prototype signing, signed-but-unwitnessed
  anchors, no consensus, no discovery, no persistence, no
  identity proofing beyond node_id, and the experiment
  007/014 server processes are watched by nothing here.
- Deliberate removal by the owner (availability case 3) is
  NOT implemented or touched here.
- No performance, scale, or latency numbers were measured or
  are claimed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from liveness import LivenessWatcher, WatchReport
from replication import NodeSpec, ReplicatedPolicy
from snapshot_delivery import DeliveryReport, SnapshotDeliveryWatcher

# The stated cycle order (case 8, stated before the build):
# delivery first, so a same-cycle heal recruits over the
# newest record; liveness second. Named once, here, and
# carried on every ComposedReport.
CYCLE_ORDER = ("delivery", "liveness")


@dataclass(frozen=True)
class ComposedReport:
    """What one composed cycle saw and did, in one place.

    delivery is the delivery watcher's DeliveryReport for this
    cycle (status delivered / delivery_refused / no_change,
    with the refusal's reason when refused); liveness is the
    liveness watcher's WatchReport for this cycle (holders
    declared lost in lost_node_ids, the automatic heal and its
    result in healing). holder_count_before is the placement's
    holder count when the cycle began (before delivery and
    before any loss), holder_count_after when it ended (after
    any heal), so a cycle that delivers, loses, and heals reads
    3 -> 3 with all three facts named, not a silent no-op.
    """

    policy_hash: str
    tick: int
    order: tuple[str, ...]
    delivery: DeliveryReport
    liveness: WatchReport
    holder_count_before: int
    holder_count_after: int

    def to_dict(self) -> dict:
        return {
            "policy_hash": self.policy_hash,
            "tick": self.tick,
            "order": list(self.order),
            "delivery": self.delivery.to_dict(),
            "liveness": self.liveness.to_dict(),
            "holder_count_before": self.holder_count_before,
            "holder_count_after": self.holder_count_after,
        }


class ComposedWatcher:
    """Drives one placement's delivery watcher and liveness
    watcher in one composed cycle, in the stated order.

    Construct with the placed policy and the candidate list an
    automatic heal may recruit from (placement's eligibility
    rules apply inside heal(), unchanged). Both watchers are
    constructed here, over the same ReplicatedPolicy, from the
    placement's current state: the delivery watcher records
    the snapshot's current facts as its baseline, the liveness
    watcher registers the current holders beating at tick 0,
    and the coordinator records the snapshot file's current
    bytes as the last good record.
    """

    order = CYCLE_ORDER

    def __init__(
        self,
        replicated: ReplicatedPolicy,
        *,
        candidates: list[NodeSpec],
        snapshot_path=None,
    ):
        self.replicated = replicated
        self.delivery = SnapshotDeliveryWatcher(
            replicated, snapshot_path=snapshot_path)
        self.liveness = LivenessWatcher(replicated, candidates=candidates)
        self.current_tick = 0
        self.reports: list[ComposedReport] = []
        self.events: list[dict] = []
        # The last record the placement actually loaded, as
        # bytes: the snapshot file's contents at construction.
        # Moved only when a delivery stands (see tick()).
        self._snapshot_path = Path(self.delivery.snapshot_path)
        self._last_good_path = Path(
            str(self._snapshot_path)
            + f".last-good-{replicated.policy_hash[:12]}"
        )
        self._last_good_bytes = self._snapshot_path.read_bytes()

    # -- introspection --------------------------------------------------------

    @property
    def last_good_path(self) -> Path:
        return self._last_good_path

    # -- the composed watch ---------------------------------------------------

    def tick(self, *, beating: list[str] | tuple[str, ...] = ()) -> ComposedReport:
        """One composed cycle: delivery first, then liveness.

        The delivery tick runs on the delivery snapshot exactly
        as experiment 022 built it. If it REFUSED, the record
        on disk is not one a heal may recruit over, so before
        the liveness tick the placement's heal path is pointed
        at the last-good sidecar (the bytes of the last record
        that stood); the liveness tick - which may declare a
        holder lost and heal from that declaration, experiment
        016 exactly - then recruits over the last good record.
        The placement's snapshot path is restored to the
        delivery snapshot before the cycle returns, whatever
        the liveness tick did, so the refusal stays pending on
        the delivery watcher's own path and is re-reported by
        later cycles until its cause is fixed. If the delivery
        STOOD, the delivered file's bytes become the last good
        record. No-change cycles touch neither path.
        """
        self.current_tick += 1
        count_before = len(self.replicated.holder_node_ids())

        delivery_report = self.delivery.tick()

        swapped_from = None
        if delivery_report.status == "delivery_refused":
            self._last_good_path.write_bytes(self._last_good_bytes)
            swapped_from = self.replicated._registry_snapshot_path
            self.replicated._registry_snapshot_path = self._last_good_path
        try:
            liveness_report = self.liveness.tick(beating=beating)
        finally:
            if swapped_from is not None:
                self.replicated._registry_snapshot_path = swapped_from
        if delivery_report.status == "delivered":
            self._last_good_bytes = self._snapshot_path.read_bytes()

        healing = liveness_report.healing
        report = ComposedReport(
            policy_hash=self.replicated.policy_hash,
            tick=self.current_tick,
            order=CYCLE_ORDER,
            delivery=delivery_report,
            liveness=liveness_report,
            holder_count_before=count_before,
            holder_count_after=len(self.replicated.holder_node_ids()),
        )
        self.events.append(
            {
                "event": "composed_cycle",
                "tick": self.current_tick,
                "order": list(CYCLE_ORDER),
                "delivery_status": delivery_report.status,
                "delivery_reason": delivery_report.reason,
                "lost_node_ids": list(liveness_report.lost_node_ids),
                "healing_status": healing.status if healing else None,
                "recruited_node_ids": (
                    list(healing.recruited_node_ids) if healing else []
                ),
                "holder_count_before": report.holder_count_before,
                "holder_count_after": report.holder_count_after,
            }
        )
        self.reports.append(report)
        return report

    # check() in the style of the two watchers: a composed cycle
    # at the current modeled moment is exactly tick() with no
    # beats recorded beyond those named; tick() is the primary
    # entry point and there is no second cycle shape to drift.
    check = tick
