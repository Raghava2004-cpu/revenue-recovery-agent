"""
The agent must not be able to see the simulation's ground truth.

`sim_propensity`, `sim_funds_at` and `sim_reachable` say whether a customer
*will* pay, when their account *will* be funded, and whether their phone number
*is* live. Those fields exist so the outcome model can score an attempt. If any
decision-making code read them, the agent would be choosing retry times by
looking up the answer, and every number on the dashboard would be fraudulent —
while still looking excellent.

This is a structural test rather than a behavioural one: it greps the source of
every module the agent decides with, so the leak is caught at the moment it's
written rather than whenever someone thinks to check.
"""
import pathlib

import pytest

ORACLE_FIELDS = ("sim_propensity", "sim_funds_at", "sim_reachable")

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Modules allowed to touch ground truth: the outcome model that owns it, the
# generator that creates it, the schema that declares it, and the detect stage
# which copies it through without branching on it.
ALLOWED = {
    BACKEND / "app" / "sim" / "outcome.py",
    BACKEND / "app" / "sim" / "generator.py",
    BACKEND / "app" / "models.py",
    BACKEND / "app" / "pipeline" / "detect.py",
    BACKEND / "app" / "main.py",          # webhook path sets them to None
}

DECISION_MODULES = sorted(
    p for pattern in ("app/pipeline/*.py", "app/policy/*.py", "app/ai/*.py")
    for p in BACKEND.glob(pattern)
)


@pytest.mark.parametrize("path", DECISION_MODULES, ids=lambda p: p.name)
def test_decision_code_cannot_read_ground_truth(path):
    if path in ALLOWED:
        pytest.skip("owns or transports ground truth without branching on it")
    source = path.read_text(encoding="utf-8")
    leaked = [f for f in ORACLE_FIELDS if f in source]
    assert not leaked, (
        f"{path.name} references simulation ground truth {leaked}. The agent "
        f"must decide from the failure signal alone."
    )


def test_detect_only_copies_ground_truth_without_branching():
    """detect.py may carry the fields onto the row, but must never test them."""
    source = (BACKEND / "app" / "pipeline" / "detect.py").read_text(encoding="utf-8")
    for field in ORACLE_FIELDS:
        for line in source.splitlines():
            if field in line:
                assert line.strip().startswith(field), (
                    f"detect.py appears to branch on {field}: {line.strip()}"
                )
