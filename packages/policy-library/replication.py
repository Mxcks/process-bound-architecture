"""
Process-Bound Architecture - Gate 4 policy availability prototype.

Placement and replication for registered policies, built on Gate 2's
PolicyNode without modifying it. Python standard library only. No
network, no database, no dependencies.

WHAT THIS PROTOTYPE DEMONSTRATES (the Gate 4 outcomes)
------------------------------------------------------
1. Negative control: a policy held by exactly one node is unavailable
   after that node goes offline. That is the honest failure named in
   docs/architecture/07-availability-risk-when-a-policy-host-goes-
   offline.md, shown as a passing test that asserts unavailability,
   not hidden.

2. With a replication floor set at registration time (a minimum
   number of independent, opted-in holder nodes, e.g. 3), the policy
   survives its hosts going offline: after the original host goes
   offline another holder serves it, and after two of three holders
   are offline the last one still serves it. "Independent" here means
   distinct node_id; holders that did not opt in to serving are never
   counted and never used.

3. The floor is reported honestly when it cannot be met. Placement
   returns a PlacementReport naming how many holders actually hold
   the policy vs the floor, whether the floor was met, and a status:
   "available" only when the floor is met, "below_floor" otherwise.
   A below-floor policy is still placed on the holders that exist
   (so the shortfall is visible and fixable), but nothing in this
   module ever calls it available.

4. Self-healing holder replacement (experiment 010, availability
   test case 4, built on outcomes 1-3 without changing them). A
   holder can be lost permanently: lose_holder() removes it, which
   is distinct from go_offline()'s temporary modeled liveness (a
   temporarily offline holder is still a holder; Gate 4 failover
   covers it, and it never triggers healing). Removal drops the
   holder count, and needs_healing() / shortfall() state the gap
   against the floor as facts. heal(candidates) recruits
   replacements from other opted-in, not-already-holding candidates
   (same eligibility rules as placement), copies the policy to each
   recruit by constructing a real Gate 2 PolicyNode over the same
   verified snapshot - so a recruit verifies and serves exactly like
   an original holder - and stops as soon as the floor is restored.
   The HealingReport pairs the count before and after with the
   floor: status "healed" only when the floor is restored,
   "below_floor" when the candidates run out first, "at_floor" when
   there was nothing to heal. When no eligible replacement exists,
   healing reports below_floor and recruits nobody; it never
   pretends it healed.

5. Anchor consultation carried into placement (experiment 020,
   full-chain integration, test case in
   tests/test-cases/full-chain.md). This is a pure wiring
   addition, not a behavior change: place_policy(),
   ReplicatedPolicy, and heal() gain optional anchor_path /
   anchor_signer / anchor_secret_hex parameters, all defaulting
   to None, passed through unchanged to the PolicyNode
   constructions placement and healing already perform. With
   the parameters omitted, holders are constructed exactly as
   outcomes 1-4 built them and no consultation happens. With
   them given, every placed holder - and every holder heal()
   later recruits - consults the anchor the way a standalone
   PolicyNode does (experiments 015/019): a rewritten or
   honestly grown snapshot is refused at holder construction
   under the anchor's own names. Nothing else about placement,
   healing, or serving changes. (This paragraph originally
   closed by restating that holders never reload; that limit
   is superseded by outcome 6 below, experiment 021.)

6. Revocation delivery to a placed policy (experiment 021,
   availability test case 6, fixing finding F1 of experiment
   020). ReplicatedPolicy gains reload_snapshot(), in the same
   style as PolicyNode.reload_snapshot (experiments 008/015):
   the caller delivers the updated record over the placement's
   shared snapshot file (following the anchor lifecycle when
   an anchor is configured: honest growth is AnchorStale until
   the grown registry is re-anchored by the same signer), then
   calls reload_snapshot(). The delivered snapshot is verified
   and anchor-consulted BEFORE any holder's view changes -
   a refused reload (tampered, stale, mismatched, badly signed
   anchor) leaves the previous serving state untouched - and
   then every holder reloads and rebuilds its view, so serve()
   raises PolicyRevoked for a revoked policy, non-revoked
   policies keep serving, and the delivered path becomes the
   snapshot heal() recruits over. With no reload called,
   behavior is exactly the pre-021 behavior: the placed object
   serves from its placement-time view. Delivery is an
   explicit call, not a push; see the limits below.

HOW IT WORKS, IN PLAIN WORDS
----------------------------
Placement happens once, at registration time (in the tests, directly
after Gate 1's register() succeeds and the registry snapshot exists):
- the caller names a floor and a list of candidate NodeSpecs;
- candidates are filtered to opted-in, distinct node_ids, in the
  order given, up to no limit (all eligible candidates become
  holders, so the count can exceed the floor);
- each holder is a real Gate 2 PolicyNode constructed over the same
  verified registry snapshot, holding the policy payload, so serving
  through a holder goes through Gate 2's full verification (hash
  chain, code_hash, policy_hash recompute) every time;
- a ReplicatedPolicy tracks which holders are online. Going offline
  is modeled by marking a holder offline: serve() skips offline
  holders and asks the next online one. If no holder is online,
  serve() raises PolicyUnavailable instead of guessing or falling
  back to a central copy: there is no central copy in this object.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- Holders are in-process PolicyNode objects on one machine over one
  shared snapshot file. There is no network transport, no discovery,
  and no placement protocol. Self-healing (experiment 010) is an
  explicit heal() call made by whoever noticed the loss, against a
  candidate list the caller supplies: nothing watches for losses,
  nothing discovers candidates, and the copy a recruit receives is
  the in-process payload dict plus the same shared snapshot file,
  not a transfer over any network. Gate 4's original statement
  stands for placement itself: the floor is enforced and reported
  at placement time, and availability_report() shows the live
  servable state; healing is the one action that now exists on top
  of the report, and only when it is invoked.
- "Offline" is a boolean flipped by the tests/demo, and "lost" is a
  removal the tests/demo perform. Nothing detects a real machine
  dying, a process crashing, or a network partition, decides on its
  own that a silent holder is lost rather than temporarily offline,
  or triggers healing automatically, and there is no liveness check
  or heartbeat (the verification gate in Gate 5 is where a liveness
  check was proposed; not built here).
- Independence is node_id distinctness only. Three holders could be
  three processes on one physical machine, in one rack, or under one
  operator, and this prototype cannot tell. Real independence
  (different machines, operators, failure domains) is a deployment
  property this code does not verify.
- Serving inherits Gate 2's limits: snapshot trust is tamper-evidence
  without an external anchor, and holders serve verified bytes
  without sandboxing or executing them. Revocation (registration
  case 6, experiment 008) is honored by PolicyNode when its snapshot
  contains the revocation entry. Holders here are constructed once,
  at placement; since experiment 021 a placed ReplicatedPolicy
  learns of a later revocation through reload_snapshot() - an
  EXPLICIT call a caller makes after delivering the updated record
  over the shared snapshot file, verified and anchor-consulted
  before any holder's view changes. Nothing calls it automatically,
  nothing pushes or broadcasts the updated record, and a placed
  policy that is never reloaded keeps serving from its
  placement-time view indefinitely: the stale window for placed
  policies is closed by a call, not by the system, and is unbounded
  until that call happens. That is stated, not fixed over.
- Deliberate removal by the owner (tests/test-cases/policy-
  availability.md case 3, dependents notified before a policy stops
  being served) is NOT implemented here. Gate 4's outcomes are the
  offline cases; removal notification remains open.
- No performance, scale, or latency numbers were measured or are
  claimed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from policy_node import PolicyNode, ServedPolicy


class PolicyUnavailable(Exception):
    """No online holder can serve this policy right now."""


@dataclass(frozen=True)
class NodeSpec:
    """One candidate holder: who the node is, and whether its
    operator opted in to serving policies (Gate 2's choice)."""

    node_id: str
    serve_policies: bool = False


@dataclass(frozen=True)
class PlacementReport:
    """What placement actually did, stated honestly.

    status is "available" only if floor_met is True; any shortfall
    reports "below_floor" with the real holder count next to the
    floor, so a reader never has to infer the gap.
    """

    policy_hash: str
    floor: int
    holder_node_ids: tuple[str, ...]
    holder_count: int
    floor_met: bool
    status: str  # "available" | "below_floor"

    def to_dict(self) -> dict:
        return {
            "policy_hash": self.policy_hash,
            "floor": self.floor,
            "holder_node_ids": list(self.holder_node_ids),
            "holder_count": self.holder_count,
            "floor_met": self.floor_met,
            "status": self.status,
        }


@dataclass(frozen=True)
class AvailabilityReport:
    """The live view: how many holders exist, how many are online,
    and whether the policy can be served right now."""

    policy_hash: str
    floor: int
    holder_count: int
    online_holder_node_ids: tuple[str, ...]
    online_count: int
    floor_met: bool  # total holders vs floor, as placed
    currently_servable: bool  # at least one holder online

    def to_dict(self) -> dict:
        return {
            "policy_hash": self.policy_hash,
            "floor": self.floor,
            "holder_count": self.holder_count,
            "online_holder_node_ids": list(self.online_holder_node_ids),
            "online_count": self.online_count,
            "floor_met": self.floor_met,
            "currently_servable": self.currently_servable,
        }


@dataclass(frozen=True)
class HealingReport:
    """What one heal() call did, stated honestly (experiment 010).

    status is "healed" only if floor_restored is True and at least
    one holder was recruited; "at_floor" when there was no shortfall
    to heal (floor_restored is True, recruited is empty); and
    "below_floor" when the candidates ran out before the floor was
    restored, with the real counts on both sides of the attempt.
    """

    policy_hash: str
    floor: int
    holder_count_before: int
    recruited_node_ids: tuple[str, ...]
    holder_count_after: int
    floor_restored: bool
    status: str  # "healed" | "at_floor" | "below_floor"

    def to_dict(self) -> dict:
        return {
            "policy_hash": self.policy_hash,
            "floor": self.floor,
            "holder_count_before": self.holder_count_before,
            "recruited_node_ids": list(self.recruited_node_ids),
            "holder_count_after": self.holder_count_after,
            "floor_restored": self.floor_restored,
            "status": self.status,
        }


class ReplicatedPolicy:
    """One registered policy placed on a set of independent holders.

    Construct through place_policy(). Holders start online;
    go_offline()/come_online() flip the modeled liveness of one
    holder. serve() walks holders in placement order, skipping
    offline ones, and returns the first online holder's verified
    ServedPolicy (Gate 2 verification runs inside PolicyNode on
    every serve). No holder online means PolicyUnavailable.

    Self-healing (experiment 010): lose_holder() removes a holder
    permanently (holder count drops; distinct from a temporary
    go_offline), and heal(candidates) recruits replacement holders
    until the floor is restored or the candidates run out. Healing
    needs the placement context (policy bytes + registry snapshot)
    that place_policy() stores on the object; a ReplicatedPolicy
    constructed without it cannot heal and heal() says so.

    Revocation delivery (experiment 021): reload_snapshot()
    receives an updated registry record (the placement's shared
    snapshot file, updated in place, or an explicit new path),
    verifies and anchor-consults it before any holder's view
    changes, and rebuilds every holder's view from it - the
    placed-policy form of PolicyNode.reload_snapshot. Without
    that call, holders keep their placement-time view.
    """

    def __init__(
        self,
        *,
        policy_hash: str,
        floor: int,
        holders: dict[str, PolicyNode],
        code: bytes | None = None,
        registry_snapshot_path: str | Path | None = None,
        anchor_path: str | Path | None = None,
        anchor_signer: str | None = None,
        anchor_secret_hex: str | None = None,
    ):
        if (anchor_signer is None) != (anchor_secret_hex is None):
            raise ValueError(
                "anchor_signer and anchor_secret_hex must be given "
                "together (a signing identity is an id plus its "
                "verification secret)"
            )
        self.policy_hash = policy_hash
        self.floor = floor
        self._holders = dict(holders)  # placement order preserved
        self._online: set[str] = set(self._holders)
        self._code = code
        self._registry_snapshot_path = (
            Path(registry_snapshot_path) if registry_snapshot_path is not None else None
        )
        # Experiment 020 wiring: the anchor consultation a heal()
        # recruit must be constructed with, carried from
        # place_policy(). All None (the default) is the pre-020
        # behavior: no consultation anywhere in this module.
        self.anchor_path = Path(anchor_path) if anchor_path is not None else None
        self.anchor_signer = anchor_signer
        self.anchor_secret_hex = anchor_secret_hex

    # -- reports ----------------------------------------------------------

    def placement_report(self) -> PlacementReport:
        count = len(self._holders)
        met = count >= self.floor
        return PlacementReport(
            policy_hash=self.policy_hash,
            floor=self.floor,
            holder_node_ids=tuple(self._holders),
            holder_count=count,
            floor_met=met,
            status="available" if met else "below_floor",
        )

    def availability_report(self) -> AvailabilityReport:
        online = tuple(n for n in self._holders if n in self._online)
        return AvailabilityReport(
            policy_hash=self.policy_hash,
            floor=self.floor,
            holder_count=len(self._holders),
            online_holder_node_ids=online,
            online_count=len(online),
            floor_met=len(self._holders) >= self.floor,
            currently_servable=len(online) >= 1,
        )

    def holder_node_ids(self) -> tuple[str, ...]:
        return tuple(self._holders)

    # -- modeled liveness ---------------------------------------------------

    def go_offline(self, node_id: str) -> None:
        if node_id not in self._holders:
            raise KeyError(f"not a holder of this policy: {node_id}")
        self._online.discard(node_id)

    def come_online(self, node_id: str) -> None:
        if node_id not in self._holders:
            raise KeyError(f"not a holder of this policy: {node_id}")
        self._online.add(node_id)

    def is_online(self, node_id: str) -> bool:
        return node_id in self._online

    # -- permanent loss and self-healing (experiment 010) -------------------

    def lose_holder(self, node_id: str) -> None:
        """Remove a holder permanently: it is gone, not offline.

        Distinct from go_offline(): a temporarily offline holder is
        still a holder and still counts toward the floor (Gate 4
        failover covers serving while it is down). A lost holder is
        removed from the holder set, so the holder count drops and
        the placement/availability reports state the shortfall.
        Losing a node that is not a holder is a KeyError, never a
        silent no-op.
        """
        if node_id not in self._holders:
            raise KeyError(f"not a holder of this policy: {node_id}")
        del self._holders[node_id]
        self._online.discard(node_id)

    def shortfall(self) -> int:
        """How many holders short of the floor this policy is now."""
        return max(0, self.floor - len(self._holders))

    def needs_healing(self) -> bool:
        """Detection, as a fact: the holder count is below the floor."""
        return self.shortfall() > 0

    def heal(self, candidates: list[NodeSpec]) -> HealingReport:
        """Recruit replacement holders until the floor is restored.

        Candidates are taken in order under placement's own rules:
        only opted-in nodes, never a node that already holds the
        policy, never the same node twice in one call. Each recruit
        is copied the policy the way placement copies it - a real
        PolicyNode over the same verified registry snapshot holding
        the policy payload - and starts online. Recruiting stops the
        moment the floor is restored; spares beyond the shortfall
        are not taken. If the candidates run out first, the report
        says below_floor with the counts as they stand: a partial
        recruitment is kept (those holders are real) and reported,
        never rounded up to healed.
        """
        before = len(self._holders)
        if not self.needs_healing():
            return HealingReport(
                policy_hash=self.policy_hash,
                floor=self.floor,
                holder_count_before=before,
                recruited_node_ids=(),
                holder_count_after=before,
                floor_restored=True,
                status="at_floor",
            )
        if self._code is None or self._registry_snapshot_path is None:
            raise RuntimeError(
                "cannot heal: this ReplicatedPolicy was constructed "
                "without the placement context (policy bytes and "
                "registry snapshot) that copying to a replacement "
                "holder requires; construct through place_policy()"
            )
        recruited: list[str] = []
        for spec in candidates:
            if not self.needs_healing():
                break
            if not spec.serve_policies:
                continue  # never counted, never used: not their choice
            if spec.node_id in self._holders:
                continue  # already a holder (or already recruited now)
            self._holders[spec.node_id] = PolicyNode(
                node_id=spec.node_id,
                registry_snapshot_path=self._registry_snapshot_path,
                payloads={self.policy_hash: self._code},
                serve_policies=True,
                anchor_path=self.anchor_path,
                anchor_signer=self.anchor_signer,
                anchor_secret_hex=self.anchor_secret_hex,
            )
            self._online.add(spec.node_id)
            recruited.append(spec.node_id)
        restored = not self.needs_healing()
        return HealingReport(
            policy_hash=self.policy_hash,
            floor=self.floor,
            holder_count_before=before,
            recruited_node_ids=tuple(recruited),
            holder_count_after=len(self._holders),
            floor_restored=restored,
            status="healed" if restored else "below_floor",
        )

    # -- receiving an updated record (experiment 021) ---------------------

    def reload_snapshot(
        self, snapshot_path: str | Path | None = None
    ) -> int:
        """Receive an updated registry record and rebuild every
        holder's view from it (availability case 6; finding F1 of
        experiment 020, fixed here).

        This is PolicyNode.reload_snapshot (experiments 008/015)
        carried to a placed policy, in the same style and with
        the same refusals: the updated snapshot - this placement's
        own shared snapshot file after a sync has copied the new
        record over it (the default), or an explicit new path -
        is verified and, when an anchor is configured, consulted
        BEFORE any holder's view changes, and only then is every
        holder reloaded from it. Returns the number of entries in
        the verified snapshot.

        The before/after discipline is two-phase, so a refused
        reload leaves the previous serving state untouched
        exactly as a standalone node's refused reload does:

        - Phase 1 validates the delivered snapshot once, through
          a throwaway PolicyNode constructed exactly as a holder
          is (same snapshot, same anchor consultation this
          placement carries): a tampered snapshot raises
          TamperedRecord, an honestly grown one AnchorStale, a
          rewritten one AnchorMismatch, a badly signed anchor
          AnchorSignatureInvalid - and no holder has been touched
          yet, so the placed policy keeps serving its previous
          view.
        - Phase 2 reloads each holder in placement order through
          its own reload_snapshot, which re-runs the same
          verification per holder before rebuilding that holder's
          view, and records the delivered path as this placement's
          snapshot, so a later heal() recruits replacements over
          the updated record, not the stale one.

        After a successful reload of a snapshot containing a
        revocation entry for this policy, serve() raises
        PolicyRevoked from the first online holder, exactly as a
        standalone node refuses. With NO reload called, nothing
        changes: the placed object keeps serving from its
        placement-time view - delivery here is an explicit call,
        not a push, and that remaining fact is stated in this
        module's limits, not hidden.
        """
        if not self._holders:
            raise PolicyUnavailable(
                f"no holders to reload for policy {self.policy_hash}"
            )
        path = (
            Path(snapshot_path)
            if snapshot_path is not None
            else self._registry_snapshot_path
        )
        if path is None:
            # A ReplicatedPolicy constructed without placement
            # context: fall back to the snapshot its first holder
            # was built over, as PolicyNode.reload_snapshot does.
            path = next(iter(self._holders.values())).registry_snapshot_path
        # Phase 1: validate once, before any holder's view changes.
        PolicyNode(
            node_id=next(iter(self._holders)),
            registry_snapshot_path=path,
            payloads={},
            serve_policies=False,
            anchor_path=self.anchor_path,
            anchor_signer=self.anchor_signer,
            anchor_secret_hex=self.anchor_secret_hex,
        )
        # Phase 2: every holder reloads (each re-verifies first,
        # per PolicyNode.reload_snapshot) and rebuilds its view.
        loaded = 0
        for node in self._holders.values():
            loaded = node.reload_snapshot(path)
        self._registry_snapshot_path = path
        return loaded

    # Backwards-readable alias, as on PolicyNode: receiving the
    # updated record *is* a sync of this placement's registry view.
    update_snapshot = reload_snapshot

    # -- serving --------------------------------------------------------------

    def serve(self) -> ServedPolicy:
        """Serve the policy from the first online holder.

        Gate 2 verification happens inside the holder's serve_policy;
        a verification refusal propagates rather than being masked by
        trying holders until one serves unverified bytes.
        """
        for node_id, node in self._holders.items():
            if node_id not in self._online:
                continue
            return node.serve_policy(self.policy_hash)
        raise PolicyUnavailable(
            f"no online holder for policy {self.policy_hash}: "
            f"{len(self._holders)} holder(s), 0 online"
        )


def place_policy(
    *,
    policy_hash: str,
    code: bytes,
    registry_snapshot_path: str | Path,
    candidates: list[NodeSpec],
    floor: int,
    anchor_path: str | Path | None = None,
    anchor_signer: str | None = None,
    anchor_secret_hex: str | None = None,
) -> ReplicatedPolicy:
    """Place a registered policy on independent opted-in holders.

    This is the registration-time placement step: call it after Gate 1
    registration has written the registry entry the snapshot copies.
    Candidates that did not opt in (serve_policies=False) and repeats
    of a node_id already placed are skipped, in order. Every eligible
    candidate becomes a holder, whether or not the floor is reached;
    whether the floor was reached is the PlacementReport's job to
    state, not this function's job to hide by refusing placement.

    Experiment 020 wiring: the optional anchor parameters are
    passed through to every holder's PolicyNode construction and
    stored on the ReplicatedPolicy for heal() to pass to recruits.
    Defaulting to None, they change nothing when omitted.
    """
    if floor < 1:
        raise ValueError("replication floor must be at least 1")
    holders: dict[str, PolicyNode] = {}
    for spec in candidates:
        if not spec.serve_policies:
            continue  # never counted, never used: not their choice
        if spec.node_id in holders:
            continue  # same node twice is not two independent holders
        holders[spec.node_id] = PolicyNode(
            node_id=spec.node_id,
            registry_snapshot_path=registry_snapshot_path,
            payloads={policy_hash: code},
            serve_policies=True,
            anchor_path=anchor_path,
            anchor_signer=anchor_signer,
            anchor_secret_hex=anchor_secret_hex,
        )
    return ReplicatedPolicy(
        policy_hash=policy_hash,
        floor=floor,
        holders=holders,
        code=code,
        registry_snapshot_path=registry_snapshot_path,
        anchor_path=anchor_path,
        anchor_signer=anchor_signer,
        anchor_secret_hex=anchor_secret_hex,
    )
