"""The audit trail's tamper-evidence has to be demonstrable, not asserted."""
from app import audit
from app.models import AuditLog


def test_chain_verifies_on_a_clean_run(batch):
    result = audit.verify_chain(batch)
    assert result["valid"] is True
    assert result["entries"] > 0


def test_editing_a_past_decision_breaks_the_chain(batch):
    """
    The whole point: someone quietly rewriting history is detected, and the
    report names the exact entry where the trail diverges.
    """
    victim = batch.query(AuditLog).filter(AuditLog.stage == "act").first()
    assert victim is not None
    original = victim.decision

    victim.decision = "Executed a totally different action, honestly."
    batch.commit()

    result = audit.verify_chain(batch)
    assert result["valid"] is False
    assert result["broken_at_seq"] == victim.seq
    assert "modified after it was written" in result["message"]

    victim.decision = original
    batch.commit()
    assert audit.verify_chain(batch)["valid"] is True


def test_deleting_an_entry_breaks_the_chain(batch):
    entries = batch.query(AuditLog).order_by(AuditLog.seq).all()
    batch.delete(entries[len(entries) // 2])
    batch.commit()

    result = audit.verify_chain(batch)
    assert result["valid"] is False


def test_every_entry_records_the_policy_it_ran_under(batch):
    from app.config import POLICY_VERSION
    for entry in batch.query(AuditLog).limit(50).all():
        assert entry.policy_version == POLICY_VERSION
