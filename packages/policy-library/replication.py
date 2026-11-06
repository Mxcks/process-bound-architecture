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
  no placement protocol, and no automatic re-replication: if a holder
  goes offline permanently, this prototype does not recruit a
  replacement holder or heal back up to the floor. The floor is
  enforced and reported at placement time only; availability_report()
  shows the live shortfall but nothing acts on it.
- "Offline" is a boolean flipped by the tests/demo. Nothing detects
  a real machine dying, a process crashing, or a network partition,
  and there is no liveness check or heartbeat (the verification gate
  in Gate 5 is where a liveness check was proposed; not built here).
- Independence is node_id distinctness only. Three holders could be
  three processes on one physical machine, in one rack, or under one
  operator, and this prototype cannot tell. Real independence
  (different machines, operators, failure domains) is a deployment
  property this code does not verify.
- Serving inherits Gate 2's limits: snapshot trust is tamper-evidence
  without an external anchor, and holders serve verified bytes
  without sandboxing or executing them. Revocation (registration
  case 6, experiment 008) is honored by PolicyNode when its snapshot
  contains the revocation entry, but holders here are constructed
  once, at placement, and never reload: a placed ReplicatedPolicy
  does not learn of a revocation recorded after its snapshot was
  taken. Wiring reload into placement is unbuilt.
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


class ReplicatedPolicy:
    """One registered policy placed on a set of independent holders.

    Construct through place_policy(). Holders start online;
    go_offline()/come_online() flip the modeled liveness of one
    holder. serve() walks holders in placement order, skipping
    offline ones, and returns the first online holder's verified
    ServedPolicy (Gate 2 verification runs inside PolicyNode on
    every serve). No holder online means PolicyUnavailable.
    """

    def __init__(
        self,
        *,
        policy_hash: str,
        floor: int,
        holders: dict[str, PolicyNode],
    ):
        self.policy_hash = policy_hash
        self.floor = floor
        self._holders = dict(holders)  # placement order preserved
        self._online: set[str] = set(self._holders)

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
) -> ReplicatedPolicy:
    """Place a registered policy on independent opted-in holders.

    This is the registration-time placement step: call it after Gate 1
    registration has written the registry entry the snapshot copies.
    Candidates that did not opt in (serve_policies=False) and repeats
    of a node_id already placed are skipped, in order. Every eligible
    candidate becomes a holder, whether or not the floor is reached;
    whether the floor was reached is the PlacementReport's job to
    state, not this function's job to hide by refusing placement.
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
        )
    return ReplicatedPolicy(policy_hash=policy_hash, floor=floor, holders=holders)
