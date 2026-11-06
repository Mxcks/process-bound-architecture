"""
Process-Bound Architecture - Gate 1 registration prototype.

A small, runnable prototype of layer 04 (Registration: the Blocker).
Python standard library only. No network, no database, no dependencies.

WHAT THIS PROTOTYPE DEMONSTRATES (the six Gate 1 outcomes)
----------------------------------------------------------
1. An authenticated registration channel exists before any use: a
   RegistrationChannel is constructed (with its registry and log files)
   before any user enrolls or registers, and it rejects work from
   identities it has not provisioned.
2. Any enrolled user can register a policy library they authored.
3. A bad signature is rejected, and the rejection is written to the
   audit log.
4. Unregistered code is blocked at an execution boundary, every time.
5. A good registration writes ONE deterministic, tamper-evident record:
   an append-only JSON-lines log where each entry is hash-chained to the
   previous entry. The chain can be re-verified at any later time, and
   registering the same library twice returns the same record instead
   of writing a second, conflicting one.
6. A validation channel identifier is bound if and only if registration
   succeeds. Rejected and unregistered code never gets one.

REVOCATION (test case 6 in tests/test-cases/registration.md, built in
experiment 008)
----------------------------------------------------------
7. A registered policy can be revoked, but only by its author, and
   only through the same authenticated channel: the caller must be
   enrolled, must be the record's author, and must present a valid
   revocation signature (HMAC over a revocation-specific canonical
   string, signed with the author's enrollment secret). A non-author
   attempt, an unknown author, an unknown policy, and a bad signature
   are all rejected and written to the audit log.
8. Revocation is a NEW entry in the same hash-chained registry log
   (event "revoked", chained to the registration entry exactly like a
   second registration would be). The original registration entry is
   never deleted or rewritten, and the chain still verifies with the
   revocation entry in it. Registering is append-only; revoking is
   append-only too.
9. After revocation, in this channel's view: the execution boundary
   refuses the policy (BlockedRevoked), and validation_channel_for
   returns None, so the policy's validation channel is no longer
   honored. The historical registration record is still readable via
   get_record (the record happened; it is the *standing* of the
   policy that changed), and is_revoked / get_revocation expose the
   new standing. Revoking twice is idempotent: the second call
   returns the existing revocation record and writes no new entry.
10. STALE WINDOW, stated not hidden: revocation takes effect wherever
    the updated record has arrived. A PolicyNode (Gate 2) holding
    only the pre-revocation snapshot does not know, and keeps
    serving, until it receives the updated record (see
    packages/policy-library/policy_node.py, reload_snapshot). There
    is no push, no broadcast, and no bound on how long a stale node
    that never syncs stays stale in this prototype.

HONEST LIMITS (prototype, NOT production crypto)
------------------------------------------------
- "Signatures" are HMAC-SHA256 with a per-user shared secret that the
  channel itself issues at enrollment. That is symmetric: the channel
  can forge any user's signature, enrollment/identity proofing is out
  of scope, and secret storage here is in-memory only. Production needs
  asymmetric keys (e.g. Ed25519), real identity proofing, and protected
  key storage. None of that is claimed here.
- The hash chain makes later edits to the log *detectable* by anyone
  holding the log; it does not make edits impossible, and it is not a
  distributed/blockchain ledger. There is no consensus, no replication,
  and no sync mesh in this package (those are later gates).
- The execution boundary *admits or blocks* code based on registration
  status and code hash. It does not sandbox or run arbitrary code;
  actually executing policy code safely is the runtime package's job.
- Everything is local files and in-process calls. There is no server,
  no HTTP, and no concurrency control beyond a simple in-process lock
  assumption (single-threaded prototype).
- Revocation inherits the signing limit above: because the channel
  issues the secrets, the channel itself could forge a revocation
  signature. Revocation is also final in this prototype: there is no
  un-revoke / reinstatement entry type, and no notification to nodes
  holding the policy (deliberate-removal notification, availability
  test case 3, remains unbuilt).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path


GENESIS_HASH = "0" * 64


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_library_bytes(author: str, name: str, version: str, code: bytes) -> bytes:
    """Deterministic encoding of a library's identity.

    The same (author, name, version, code) always produces the same bytes,
    and therefore the same policy hash, signature input, and record.
    """
    header = f"{author}\n{name}\n{version}\n{_sha256_hex(code)}\n".encode("utf-8")
    return header + code


def policy_hash_for(author: str, name: str, version: str, code: bytes) -> str:
    return _sha256_hex(canonical_library_bytes(author, name, version, code))


def sign_library(secret_hex: str, author: str, name: str, version: str, code: bytes) -> str:
    """Prototype 'signature': HMAC-SHA256 over the canonical library bytes.

    See the module docstring for why this is NOT production signing.
    """
    return hmac.new(
        bytes.fromhex(secret_hex),
        canonical_library_bytes(author, name, version, code),
        hashlib.sha256,
    ).hexdigest()


def canonical_revocation_bytes(policy_hash: str) -> bytes:
    """Deterministic encoding of a revocation request's identity.

    Domain-separated from library signing (different prefix, no code),
    so a registration signature can never be replayed as a revocation
    signature or the other way round.
    """
    return f"PBA-REVOKE-v1|{policy_hash}".encode("utf-8")


def sign_revocation(secret_hex: str, policy_hash: str) -> str:
    """Prototype revocation 'signature': HMAC-SHA256 over the canonical
    revocation bytes, with the author's enrollment secret.

    See the module docstring for why this is NOT production signing.
    """
    return hmac.new(
        bytes.fromhex(secret_hex),
        canonical_revocation_bytes(policy_hash),
        hashlib.sha256,
    ).hexdigest()


class RegistrationRejected(Exception):
    """Registration failed; the attempt was written to the audit log."""


class RevocationRejected(RegistrationRejected):
    """Revocation failed; the attempt was written to the audit log.

    Subclasses RegistrationRejected so existing callers that catch
    channel rejections catch revocation rejections too.
    """


class BlockedUnregistered(Exception):
    """Execution boundary refused code that is not registered (or mismatched)."""


class BlockedRevoked(BlockedUnregistered):
    """Execution boundary refused code whose registration was revoked.

    Subclasses BlockedUnregistered so existing callers that treat any
    boundary refusal alike keep working; callers that care can catch
    this type specifically.
    """


@dataclass(frozen=True)
class RegistrationRecord:
    policy_hash: str
    author: str
    name: str
    version: str
    code_hash: str
    validation_channel: str
    log_index: int
    record_hash: str

    def to_dict(self) -> dict:
        return {
            "policy_hash": self.policy_hash,
            "author": self.author,
            "name": self.name,
            "version": self.version,
            "code_hash": self.code_hash,
            "validation_channel": self.validation_channel,
            "log_index": self.log_index,
            "record_hash": self.record_hash,
        }


@dataclass(frozen=True)
class RevocationRecord:
    """The revocation entry for one policy, as written to the chain.

    This is a separate record from RegistrationRecord on purpose: the
    registration record is historical fact and is never mutated, so
    revocation standing lives here, keyed by the same policy hash.
    """

    policy_hash: str
    author: str
    validation_channel: str  # the channel that is no longer honored
    log_index: int
    record_hash: str

    def to_dict(self) -> dict:
        return {
            "policy_hash": self.policy_hash,
            "author": self.author,
            "validation_channel": self.validation_channel,
            "log_index": self.log_index,
            "record_hash": self.record_hash,
        }


class RegistrationChannel:
    """The authenticated registration channel.

    Construct it first (Gate 1, outcome 1): construction creates the
    registry log and audit log files, loads any existing chain, and only
    then can users be enrolled and libraries registered through it.

    Secrets issued at enrollment live only in this object's memory in the
    prototype. Losing the process loses the secrets; the on-disk hash
    chain remains verifiable without them.
    """

    def __init__(self, registry_log_path: str | Path, audit_log_path: str | Path):
        self.registry_log_path = Path(registry_log_path)
        self.audit_log_path = Path(audit_log_path)
        self.registry_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_log_path.touch(exist_ok=True)
        self.audit_log_path.touch(exist_ok=True)

        self._secrets: dict[str, str] = {}  # user_id -> secret hex (memory only)
        self._records: dict[str, RegistrationRecord] = {}  # policy_hash -> record
        self._revocations: dict[str, RevocationRecord] = {}  # policy_hash -> revocation
        self._lock = threading.Lock()
        self._load_existing_chain()

    # -- channel readiness (outcome 1) ------------------------------------

    def is_ready(self) -> bool:
        """True once the channel exists with its logs in place.

        This is checkable before any enrollment or registration, which is
        the point of outcome 1: the channel exists *before* it is needed.
        """
        return self.registry_log_path.exists() and self.audit_log_path.exists()

    # -- enrollment ---------------------------------------------------------

    def enroll_user(self, user_id: str) -> str:
        """Provision an identity on the channel and return its secret once.

        Any user id may be enrolled (outcome 2 starts here). Re-enrolling
        the same user id raises, so identities cannot be silently re-keyed.
        """
        with self._lock:
            if user_id in self._secrets:
                raise ValueError(f"user already enrolled: {user_id}")
            secret_hex = secrets.token_hex(32)
            self._secrets[user_id] = secret_hex
            self._audit({"event": "enrolled", "author": user_id})
            return secret_hex

    def is_enrolled(self, user_id: str) -> bool:
        return user_id in self._secrets

    # -- registration -------------------------------------------------------

    def register(
        self,
        *,
        author: str,
        name: str,
        version: str,
        code: bytes,
        signature: str,
    ) -> tuple[RegistrationRecord, bool]:
        """Register a signed library. Returns (record, created).

        created=False means this exact library was already registered and
        the existing deterministic record was returned unchanged; no second
        log entry is written (test case 4 in tests/test-cases/registration.md).
        Raises RegistrationRejected (and audit-logs the attempt) for an
        unknown author or a bad signature.
        """
        with self._lock:
            secret_hex = self._secrets.get(author)
            if secret_hex is None:
                self._audit(
                    {
                        "event": "rejected",
                        "reason": "unknown_author",
                        "author": author,
                        "name": name,
                        "version": version,
                    }
                )
                raise RegistrationRejected(f"unknown author (not enrolled): {author}")

            expected = sign_library(secret_hex, author, name, version, code)
            if not hmac.compare_digest(expected, signature):
                self._audit(
                    {
                        "event": "rejected",
                        "reason": "bad_signature",
                        "author": author,
                        "name": name,
                        "version": version,
                        "code_hash": _sha256_hex(code),
                    }
                )
                raise RegistrationRejected("bad signature")

            policy_hash = policy_hash_for(author, name, version, code)
            existing = self._records.get(policy_hash)
            if existing is not None:
                self._audit(
                    {
                        "event": "duplicate_registration",
                        "author": author,
                        "policy_hash": policy_hash,
                    }
                )
                return existing, False

            # Outcome 6: the validation channel is bound here, and only here,
            # inside a successful first registration. It is a deterministic
            # function of the policy hash, so re-verifying gives the same one.
            validation_channel = "val-" + _sha256_hex(
                f"validation-channel:{policy_hash}".encode("utf-8")
            )[:32]

            entries = self._read_registry_entries()
            prev_hash = entries[-1]["entry_hash"] if entries else GENESIS_HASH
            log_index = len(entries)

            entry_body = {
                "event": "registered",
                "log_index": log_index,
                "policy_hash": policy_hash,
                "author": author,
                "name": name,
                "version": version,
                "code_hash": _sha256_hex(code),
                "validation_channel": validation_channel,
                "prev_hash": prev_hash,
            }
            entry_hash = _sha256_hex(
                json.dumps(entry_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            entry = dict(entry_body)
            entry["entry_hash"] = entry_hash
            with self.registry_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")

            record = RegistrationRecord(
                policy_hash=policy_hash,
                author=author,
                name=name,
                version=version,
                code_hash=_sha256_hex(code),
                validation_channel=validation_channel,
                log_index=log_index,
                record_hash=entry_hash,
            )
            self._records[policy_hash] = record
            self._audit(
                {
                    "event": "registered",
                    "author": author,
                    "policy_hash": policy_hash,
                    "validation_channel": validation_channel,
                }
            )
            return record, True

    # -- revocation (test case 6) -------------------------------------------

    def revoke(
        self,
        *,
        author: str,
        policy_hash: str,
        signature: str,
    ) -> tuple[RevocationRecord, bool]:
        """Revoke a registered policy. Returns (revocation, created).

        Only the policy's author can revoke it, authenticated the same
        way registration is: the caller must be enrolled on this
        channel, must be the record's author, and must present the
        revocation signature for this policy hash under their own
        enrollment secret (sign_revocation). Every failure raises
        RevocationRejected and is written to the audit log; nothing is
        appended to the registry chain on failure.

        On success exactly one NEW entry (event "revoked") is appended
        to the hash chain; the original registration entry is not
        deleted or rewritten. created=False means this policy was
        already revoked and the existing revocation record was
        returned unchanged; no second entry is written.
        """
        with self._lock:
            secret_hex = self._secrets.get(author)
            if secret_hex is None:
                self._audit(
                    {
                        "event": "revocation_rejected",
                        "reason": "unknown_author",
                        "author": author,
                        "policy_hash": policy_hash,
                    }
                )
                raise RevocationRejected(f"unknown author (not enrolled): {author}")

            record = self._records.get(policy_hash)
            if record is None:
                self._audit(
                    {
                        "event": "revocation_rejected",
                        "reason": "unknown_policy",
                        "author": author,
                        "policy_hash": policy_hash,
                    }
                )
                raise RevocationRejected(f"no registration for policy hash: {policy_hash}")

            # Ownership is checked before the signature, so an enrolled
            # non-author signing correctly *as themselves* is rejected
            # for the honest reason (not_author), not bad_signature.
            if record.author != author:
                self._audit(
                    {
                        "event": "revocation_rejected",
                        "reason": "not_author",
                        "author": author,
                        "policy_author": record.author,
                        "policy_hash": policy_hash,
                    }
                )
                raise RevocationRejected(
                    f"only the author ({record.author}) can revoke this policy"
                )

            expected = sign_revocation(secret_hex, policy_hash)
            if not hmac.compare_digest(expected, signature):
                self._audit(
                    {
                        "event": "revocation_rejected",
                        "reason": "bad_signature",
                        "author": author,
                        "policy_hash": policy_hash,
                    }
                )
                raise RevocationRejected("bad signature")

            existing = self._revocations.get(policy_hash)
            if existing is not None:
                self._audit(
                    {
                        "event": "duplicate_revocation",
                        "author": author,
                        "policy_hash": policy_hash,
                    }
                )
                return existing, False

            entries = self._read_registry_entries()
            prev_hash = entries[-1]["entry_hash"] if entries else GENESIS_HASH
            log_index = len(entries)

            entry_body = {
                "event": "revoked",
                "log_index": log_index,
                "policy_hash": policy_hash,
                "author": author,
                "revoked_by": author,
                "validation_channel": record.validation_channel,
                "prev_hash": prev_hash,
            }
            entry_hash = _sha256_hex(
                json.dumps(entry_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            entry = dict(entry_body)
            entry["entry_hash"] = entry_hash
            with self.registry_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")

            revocation = RevocationRecord(
                policy_hash=policy_hash,
                author=author,
                validation_channel=record.validation_channel,
                log_index=log_index,
                record_hash=entry_hash,
            )
            self._revocations[policy_hash] = revocation
            self._audit(
                {
                    "event": "revoked",
                    "author": author,
                    "policy_hash": policy_hash,
                    "validation_channel": record.validation_channel,
                }
            )
            return revocation, True

    # -- lookups used by the execution boundary ----------------------------

    def get_record(self, policy_hash: str) -> RegistrationRecord | None:
        """The historical registration record, revoked or not.

        Registration happened and stays readable; whether the policy
        still stands is is_revoked()'s question, not this one's.
        """
        return self._records.get(policy_hash)

    def is_revoked(self, policy_hash: str) -> bool:
        return policy_hash in self._revocations

    def get_revocation(self, policy_hash: str) -> RevocationRecord | None:
        return self._revocations.get(policy_hash)

    def validation_channel_for(self, policy_hash: str) -> str | None:
        """The policy's validation channel, or None if it is not honored:
        never registered, or registered and since revoked (case 6)."""
        if policy_hash in self._revocations:
            return None
        record = self._records.get(policy_hash)
        return record.validation_channel if record else None

    # -- tamper evidence (outcome 5) ----------------------------------------

    def _read_registry_entries(self) -> list[dict]:
        entries = []
        for line in self.registry_log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries

    def _load_existing_chain(self) -> None:
        for entry in self._read_registry_entries():
            if entry.get("event") == "revoked":
                revocation = RevocationRecord(
                    policy_hash=entry["policy_hash"],
                    author=entry["author"],
                    validation_channel=entry["validation_channel"],
                    log_index=entry["log_index"],
                    record_hash=entry["entry_hash"],
                )
                self._revocations[revocation.policy_hash] = revocation
                continue
            record = RegistrationRecord(
                policy_hash=entry["policy_hash"],
                author=entry["author"],
                name=entry["name"],
                version=entry["version"],
                code_hash=entry["code_hash"],
                validation_channel=entry["validation_channel"],
                log_index=entry["log_index"],
                record_hash=entry["entry_hash"],
            )
            self._records[record.policy_hash] = record

    def verify_registry_chain(self) -> bool:
        """Recompute the whole hash chain. False means the log was altered."""
        prev_hash = GENESIS_HASH
        for expected_index, entry in enumerate(self._read_registry_entries()):
            if entry.get("log_index") != expected_index:
                return False
            if entry.get("prev_hash") != prev_hash:
                return False
            body = {k: v for k, v in entry.items() if k != "entry_hash"}
            recomputed = _sha256_hex(
                json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            if not hmac.compare_digest(recomputed, entry.get("entry_hash", "")):
                return False
            prev_hash = entry["entry_hash"]
        return True

    # -- audit ---------------------------------------------------------------

    def _audit(self, event: dict) -> None:
        with self.audit_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")

    def read_audit_events(self) -> list[dict]:
        events = []
        for line in self.audit_log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events


class ExecutionBoundary:
    """The execution boundary: code runs (is admitted) only if registered.

    'Admitted' here means the boundary returns a handle for the registered
    library after checking (a) a record exists for the policy hash,
    (b) the presented code hashes to the registered code hash, and
    (c) the policy has not been revoked (case 6: a revoked policy is
    refused with BlockedRevoked even when its code matches exactly).
    Anything else raises BlockedUnregistered. Actually sandboxing
    admitted code is the runtime package's job, not this prototype's
    (see module docstring).
    """

    def __init__(self, channel: RegistrationChannel):
        self._channel = channel

    def admit(self, *, policy_hash: str, code: bytes) -> RegistrationRecord:
        record = self._channel.get_record(policy_hash)
        if record is None:
            raise BlockedUnregistered(f"no registration for policy hash: {policy_hash}")
        if self._channel.is_revoked(policy_hash):
            raise BlockedRevoked(
                f"registration revoked by its author: {policy_hash}"
            )
        if _sha256_hex(code) != record.code_hash:
            raise BlockedUnregistered(
                "presented code does not match the registered code hash"
            )
        return record
