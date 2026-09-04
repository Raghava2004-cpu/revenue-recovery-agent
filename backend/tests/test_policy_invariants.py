"""
The safety properties that must hold for every case in every batch.

These aren't unit tests of individual functions — they're assertions over the
whole run. If a future playbook edit reintroduces "retry the expired card three
times", this is what catches it.
"""
from app import taxonomy as tx
from app.models import RecoveryAction, RevenueEvent


def _agent_actions(db):
    return (db.query(RecoveryAction, RevenueEvent)
            .join(RevenueEvent, RecoveryAction.event_id == RevenueEvent.id)
            .filter(RevenueEvent.arm == "agent").all())


def test_never_retries_an_instrument_that_cannot_succeed(batch):
    """An expired card or a revoked mandate is never re-presented."""
    for action, event in _agent_actions(batch):
        cause = tx.root_cause(event.root_cause)
        if cause.needs_new_instrument:
            assert action.action_type not in (tx.RETRY_SAME_INSTRUMENT, tx.RETRY_MANDATE), (
                f"Re-presented a dead instrument on {event.root_cause} "
                f"(event {event.id})"
            )


def test_hard_stop_causes_are_never_contacted(batch):
    """Risk-blocked payments and disputed invoices get no autonomous outreach."""
    for action, event in _agent_actions(batch):
        if tx.root_cause(event.root_cause).hard_stop:
            assert action.action_type in (tx.ESCALATE_HUMAN, tx.SUPPRESS), (
                f"Took '{action.action_type}' on hard-stop cause "
                f"{event.root_cause} (event {event.id})"
            )


def test_do_not_contact_is_absolute(batch):
    """DNC holds in both arms — it is the legal floor, not a tunable."""
    dnc_ids = {e.id for e in batch.query(RevenueEvent)
               .filter(RevenueEvent.do_not_contact.is_(True)).all()}
    contacts = (batch.query(RecoveryAction)
                .filter(RecoveryAction.event_id.in_(dnc_ids or {-1}),
                        RecoveryAction.channel.in_([tx.SMS, tx.WHATSAPP,
                                                    tx.VOICE, tx.EMAIL])).all())
    assert contacts == [], f"Contacted {len(contacts)} do-not-contact customers"


def test_channel_consent_is_respected(batch):
    for action, event in _agent_actions(batch):
        if action.channel == tx.WHATSAPP:
            assert event.consent_whatsapp
        elif action.channel == tx.VOICE:
            assert event.consent_voice
        elif action.channel == tx.SMS:
            assert event.consent_sms


def test_every_case_reaches_a_terminal_state(batch):
    """No case is left silently pending — an agent that can't stop is a spammer."""
    open_cases = (batch.query(RevenueEvent)
                  .filter(RevenueEvent.status.in_(["detected", "in_recovery"])).all())
    assert open_cases == [], f"{len(open_cases)} cases never terminated"


def test_contact_budget_is_never_exceeded(batch):
    from app.policy.playbooks import playbook_for

    for event in batch.query(RevenueEvent).filter(RevenueEvent.arm == "agent").all():
        budget = playbook_for(event.root_cause, "agent").max_contacts
        assert event.contact_count <= max(budget, 0), (
            f"Event {event.id} made {event.contact_count} contacts against a "
            f"budget of {budget}"
        )


def test_escalated_cases_are_never_counted_as_recovered(batch):
    for event in batch.query(RevenueEvent).all():
        if event.status in ("escalated", "suppressed", "exhausted"):
            assert (event.amount_recovered or 0.0) == 0.0
