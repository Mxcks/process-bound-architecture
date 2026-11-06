"""
Process-Bound Architecture - automatic snapshot delivery to a
placed policy (experiment 022, availability test case 7).

Built on experiment 021's ReplicatedPolicy.reload_snapshot()
without modifying it, and in the style of experiment 016's
liveness watcher: one watcher over one placed policy, a
deterministic modeled clock (an integer tick counter the caller
advances), a report per check, and a plain-dict event log the
tests assert. Python standard library only. No network, no
database, no dependencies, and no wall-clock time anywhere in
this module.

WHAT THIS PROTOTYPE DEMONSTRATES (the case 7 outcomes)
------------------------------------------------------
1. Change detection is cheap and is a comparison, not a load.
   On each check the watcher reads the registry snapshot file
   the placed policy was built over and computes its presented
   facts: the entry count and the chain head (the last entry's
   entry_hash - the same two facts the anchor of experiment 012
   pins), plus a SHA-256 fingerprint of the file's raw bytes.
   The fingerprint is part of the change fact for one honest
   reason: head and count alone cannot see an in-place edit
   that leaves both untouched (experiment 021's tampered
   delivery edits an entry body and leaves its entry_hash
   field standing), and a watcher that cannot see a tamper
   cannot refuse it. Head and count are what the reports and
   events record; the fingerprint only decides "changed".

2. A clean change is delivered in the check that notices it.
   The watcher calls the placed policy's reload_snapshot() -
   experiment 021's method, reused exactly, with its two-phase
   verify-and-consult-before-any-view-changes discipline - and
   records a "snapshot_delivered" event and a DeliveryReport
   carrying the before/after heads and entry counts and the
   number of entries the reload loaded. Nobody else calls the
   reload on this path: the trigger is the check, the way
   experiment 016's trigger is its check.

3. A refused delivery is refused by the reload itself, under
   the reload's own names, and is re-reported, never dropped.
   reload_snapshot() raises TamperedRecord for a tampered
   snapshot, AnchorStale for honest growth whose anchor
   lifecycle was not followed, AnchorMismatch for a rewrite,
   and AnchorSignatureInvalid for a badly signed anchor (the
   AnchorRefused subclasses carry their status string, which
   becomes the report's reason verbatim; TamperedRecord maps
   to "tampered_record"). On a refusal the watcher records a
   "snapshot_delivery_refused" event with the reason, keeps the
   placed policy's last-loaded facts unchanged, and so every
   later check re-attempts the same delivery and re-reports the
   same refusal until the cause is fixed - exactly the
   re-reporting discipline experiment 016 set for below-floor.
   When the cause IS fixed (for anchor_stale, the same-signer
   re-anchor of experiments 012/019), the next check delivers.

4. No change means no work. When the presented facts equal the
   last-loaded facts, the check reports "no_change" and calls
   nothing: holder views are not rebuilt on unchanged ticks.

5. The baseline is construction-time and honest about it: the
   watcher records, at construction, the facts of the snapshot
   the placed policy was built over (or last reloaded to), and
   delivers only changes presented after that. It detects that
   the snapshot FILE changed; how the new record got into the
   file is the synchronization layer's delivery, exactly as in
   every earlier experiment - this module is the receiving
   trigger, not a transport.

HOW IT WORKS, IN PLAIN WORDS
----------------------------
SnapshotDeliveryWatcher binds one ReplicatedPolicy and the
snapshot path that policy's holders were built over (taken
from the placement itself unless a path is given explicitly).
tick() is one modeled moment: advance one tick, then check().
check() computes the presented facts, compares them with the
last-loaded facts, and - only on a difference - calls
reload_snapshot() on the placed policy, recording the outcome
either way. A successful delivery moves the last-loaded facts
to the presented facts, so the next unchanged check is quiet
again. Every check returns a DeliveryReport and appends it to
watcher.reports; deliveries and refusals also append plain-dict
events to watcher.events, which the tests assert.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- "Automatic" means precisely "triggered by check()/tick()
  without a separate reload call". No timer, scheduler, file
  watcher, or background thread fires check() unattended, and
  the modeled clock is a counter the caller advances; nothing
  here notices a file change between checks.
- One watcher watches one placed policy over one snapshot
  file, in-process, on one machine. Two placed policies over
  the same snapshot each need their own watcher (the tests run
  exactly that), and nothing coordinates two watchers, moves a
  record over a network, or delivers to the experiment 007/014
  server processes - a running server still learns of a
  revocation only on restart, as experiment 021 stated.
- Detection reads the whole snapshot file to fingerprint it
  and parse its head and count. That is cheap next to
  reload_snapshot()'s full chain verification, which still
  runs on every delivered change - this module removes the
  human from the trigger, not the verification from the
  delivery, and no performance number is measured or claimed.
- A refusal's cause is fixed outside the watcher (re-anchor,
  re-deliver an untampered record); the watcher never repairs,
  re-anchors, or edits the snapshot itself, and never serves
  from a snapshot the reload refused.
- Deliberate removal by the owner (availability case 3) is
  NOT implemented or touched here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class SnapshotFacts:
    """The cheap presentation facts of one snapshot file.

    count and head are the facts experiments 012/015 pin and
    consult; fingerprint is the SHA-256 of the file's raw
    bytes, carried in the change comparison only (see the
    module docstring for why head+count alone are not enough
    to see an in-place tamper).
    """

    count: int
    head: str
    fingerprint: str


def snapshot_facts(snapshot_path: str | Path) -> SnapshotFacts:
    """Compute the presented facts of a snapshot file.

    Reads the file's bytes, fingerprints them, and parses the
    JSON-lines entries for the count and the last entry's
    entry_hash. This verifies nothing: verification is
    reload_snapshot()'s job, and it runs before any holder's
    view changes when a change is delivered.
    """
    raw = Path(snapshot_path).read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    count = 0
    head = GENESIS_HASH
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        count += 1
        head = entry.get("entry_hash", GENESIS_HASH)
    return SnapshotFacts(count=count, head=head, fingerprint=fingerprint)


def _refusal_reason(exc: Exception) -> str:
    """The refusal's own name, as the report's reason.

    The anchor refusals (experiment 015/019) carry a status
    string on the exception class ("anchor_stale",
    "anchor_mismatch", "anchor_signature_invalid"); that string
    is used verbatim. TamperedRecord is named here, in the same
    snake-case vocabulary. Anything else is reported under its
    exception class name, never hidden behind a generic label.
    """
    status = getattr(exc, "status", None)
    if isinstance(status, str):
        return status
    if type(exc).__name__ == "TamperedRecord":
        return "tampered_record"
    return type(exc).__name__


@dataclass(frozen=True)
class DeliveryReport:
    """What one check() saw and did, stated honestly.

    status is "delivered" only when this check's automatic
    reload loaded the presented snapshot, "delivery_refused"
    when the snapshot changed and the reload refused it (with
    reason naming the refusal, and after_* still the PRESENTED
    facts - the facts that were refused), and "no_change" when
    the presented facts equal the last-loaded facts (before_*
    and after_* are both those facts and nothing was called).
    loaded_entries is the entry count reload_snapshot()
    returned, or None when no reload stood.
    """

    policy_hash: str
    tick: int
    status: str  # "delivered" | "delivery_refused" | "no_change"
    before_head: str
    after_head: str
    before_count: int
    after_count: int
    loaded_entries: int | None
    reason: str | None

    def to_dict(self) -> dict:
        return {
            "policy_hash": self.policy_hash,
            "tick": self.tick,
            "status": self.status,
            "before_head": self.before_head,
            "after_head": self.after_head,
            "before_count": self.before_count,
            "after_count": self.after_count,
            "loaded_entries": self.loaded_entries,
            "reason": self.reason,
        }


class SnapshotDeliveryWatcher:
    """Watches one ReplicatedPolicy's snapshot and delivers
    changes to it automatically, in the style of experiment
    016's LivenessWatcher.

    Construct with the placed policy; the watched snapshot is
    the placement's own registry snapshot (the file its holders
    were built over and reload_snapshot() defaults to), unless
    snapshot_path names another file explicitly. Construction
    records that snapshot's current facts as the last-loaded
    baseline and delivers nothing.
    """

    def __init__(self, replicated, *, snapshot_path=None):
        self.replicated = replicated
        if snapshot_path is not None:
            self.snapshot_path = Path(snapshot_path)
        elif getattr(replicated, "_registry_snapshot_path", None) is not None:
            self.snapshot_path = Path(replicated._registry_snapshot_path)
        else:
            first_holder = next(iter(replicated._holders.values()))
            self.snapshot_path = Path(first_holder.registry_snapshot_path)
        self.current_tick = 0
        self.reports: list[DeliveryReport] = []
        self.events: list[dict] = []
        self._last_loaded: SnapshotFacts = snapshot_facts(self.snapshot_path)

    # -- the modeled clock --------------------------------------------------

    def tick(self) -> DeliveryReport:
        """One modeled moment: advance one tick, then check."""
        self.current_tick += 1
        return self.check()

    # -- introspection --------------------------------------------------------

    @property
    def last_loaded_head(self) -> str:
        return self._last_loaded.head

    @property
    def last_loaded_count(self) -> int:
        return self._last_loaded.count

    # -- the watch ------------------------------------------------------------

    def check(self) -> DeliveryReport:
        """Compare the presented snapshot with the last-loaded
        one; deliver a clean change in this same check.

        No difference: report "no_change" and call nothing. A
        difference: call the placed policy's reload_snapshot()
        (experiment 021, reused exactly). Its success is a
        delivery, recorded with the before/after heads and
        counts, and the last-loaded facts move to the presented
        facts. Its refusal propagates as this check's report
        under the refusal's own name, the last-loaded facts stay
        where they were, and the next check re-attempts and
        re-reports - a refused delivery never goes silent.
        """
        before = self._last_loaded
        try:
            presented = snapshot_facts(self.snapshot_path)
        except Exception as exc:  # unreadable or malformed file
            report = DeliveryReport(
                policy_hash=self.replicated.policy_hash,
                tick=self.current_tick,
                status="delivery_refused",
                before_head=before.head,
                after_head=before.head,
                before_count=before.count,
                after_count=before.count,
                loaded_entries=None,
                reason=_refusal_reason(exc),
            )
            self.events.append(
                {
                    "event": "snapshot_delivery_refused",
                    "tick": self.current_tick,
                    "reason": report.reason,
                    "before_head": before.head,
                    "after_head": before.head,
                    "before_count": before.count,
                    "after_count": before.count,
                }
            )
            self.reports.append(report)
            return report

        if presented == before:
            report = DeliveryReport(
                policy_hash=self.replicated.policy_hash,
                tick=self.current_tick,
                status="no_change",
                before_head=before.head,
                after_head=presented.head,
                before_count=before.count,
                after_count=presented.count,
                loaded_entries=None,
                reason=None,
            )
            self.reports.append(report)
            return report

        try:
            loaded = self.replicated.reload_snapshot(self.snapshot_path)
        except Exception as exc:
            reason = _refusal_reason(exc)
            report = DeliveryReport(
                policy_hash=self.replicated.policy_hash,
                tick=self.current_tick,
                status="delivery_refused",
                before_head=before.head,
                after_head=presented.head,
                before_count=before.count,
                after_count=presented.count,
                loaded_entries=None,
                reason=reason,
            )
            self.events.append(
                {
                    "event": "snapshot_delivery_refused",
                    "tick": self.current_tick,
                    "reason": reason,
                    "before_head": before.head,
                    "after_head": presented.head,
                    "before_count": before.count,
                    "after_count": presented.count,
                }
            )
            self.reports.append(report)
            return report

        self._last_loaded = presented
        report = DeliveryReport(
            policy_hash=self.replicated.policy_hash,
            tick=self.current_tick,
            status="delivered",
            before_head=before.head,
            after_head=presented.head,
            before_count=before.count,
            after_count=presented.count,
            loaded_entries=loaded,
            reason=None,
        )
        self.events.append(
            {
                "event": "snapshot_delivered",
                "tick": self.current_tick,
                "before_head": before.head,
                "after_head": presented.head,
                "before_count": before.count,
                "after_count": presented.count,
                "loaded_entries": loaded,
            }
        )
        self.reports.append(report)
        return report
