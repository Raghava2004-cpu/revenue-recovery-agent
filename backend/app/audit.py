"""
Tamper-evident audit trail.

Requirement being met: "compliant escalation, stopping rules, and an audit
trail". A log you can silently edit is not an audit trail, so every entry
commits to the one before it:

    entry_hash = sha256(prev_hash || seq || event_id || stage || decision || detail || occurred_at)

Changing or deleting any historical row breaks every hash after it, which
GET /audit/verify detects and reports with the exact sequence number where the
chain first diverges. tests/test_audit_chain.py proves this by mutating a row.
"""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import POLICY_VERSION
from app.models import AuditLog, utcnow

GENESIS = "0" * 64


def _canonical(payload: dict | None) -> str:
    """Stable JSON — sorted keys, no incidental whitespace — so hashes reproduce."""
    if not payload:
        return ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _stamp(ts: datetime) -> str:
    """
    A timestamp representation that survives a database round trip.

    SQLite has no native datetime type and hands back naive values, so hashing
    `ts.isoformat()` directly produced one string on write ("...+00:00") and a
    different one on read — every entry failed verification on a clean run.
    Normalising to UTC and dropping the offset makes the digest depend on the
    instant rather than on how the driver happened to spell it.
    """
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.isoformat(timespec="microseconds")


def _digest(prev_hash, seq, event_id, stage, decision, detail, occurred_at) -> str:
    material = "|".join([
        prev_hash or GENESIS, str(seq), str(event_id or ""), stage,
        decision, detail or "", _stamp(occurred_at),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _tip(db: Session) -> tuple[int, str]:
    """(next_seq, prev_hash) for the current end of the chain."""
    row = db.query(AuditLog.seq, AuditLog.entry_hash).order_by(AuditLog.seq.desc()).first()
    if row is None:
        return 0, GENESIS
    return row.seq + 1, row.entry_hash


def record(
    db: Session,
    *,
    stage: str,
    decision: str,
    event_id: int | None = None,
    detail: dict | None = None,
    occurred_at: datetime | None = None,
    commit: bool = True,
) -> AuditLog:
    """Append one entry. `occurred_at` is the simulation clock, not wall time."""
    seq, prev_hash = _tip(db)
    occurred_at = occurred_at or utcnow()
    detail_json = _canonical(detail)

    entry = AuditLog(
        event_id=event_id,
        seq=seq,
        stage=stage,
        decision=decision,
        detail=detail_json or None,
        policy_version=POLICY_VERSION,
        occurred_at=occurred_at,
        prev_hash=prev_hash,
        entry_hash=_digest(prev_hash, seq, event_id, stage, decision, detail_json, occurred_at),
    )
    db.add(entry)
    if commit:
        db.commit()
    else:
        db.flush()
    return entry


def verify_chain(db: Session) -> dict:
    """
    Walk the chain from genesis and recompute every hash.

    Returns the first divergence rather than just a boolean — when an auditor
    asks "what changed", the answer should be a sequence number.
    """
    total = db.query(func.count(AuditLog.id)).scalar() or 0
    if total == 0:
        return {"valid": True, "entries": 0, "message": "Empty chain."}

    prev_hash = GENESIS
    checked = 0
    for row in db.query(AuditLog).order_by(AuditLog.seq).yield_per(500):
        if row.prev_hash != prev_hash:
            return {
                "valid": False, "entries": total, "broken_at_seq": row.seq,
                "message": f"Chain link broken at seq {row.seq}: entry does not "
                           f"point at the previous entry's hash.",
            }
        expected = _digest(row.prev_hash, row.seq, row.event_id, row.stage,
                           row.decision, row.detail, row.occurred_at)
        if expected != row.entry_hash:
            return {
                "valid": False, "entries": total, "broken_at_seq": row.seq,
                "message": f"Entry at seq {row.seq} was modified after it was "
                           f"written — recomputed hash does not match stored hash.",
            }
        prev_hash = row.entry_hash
        checked += 1

    return {
        "valid": True, "entries": checked, "head_hash": prev_hash,
        "policy_version": POLICY_VERSION,
        "message": f"All {checked} entries verified against the hash chain.",
    }
