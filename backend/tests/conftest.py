import os
import tempfile

import pytest

# Every test runs against its own throwaway database, set before app.config is
# imported anywhere.
_TMP = tempfile.mkdtemp(prefix="rra-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ.pop("ANTHROPIC_API_KEY", None)   # tests never hit a live LLM


@pytest.fixture()
def db():
    from app.db import SessionLocal, engine
    from app.models import Base, init_db

    Base.metadata.drop_all(bind=engine)
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def batch(db):
    """A small, fully-run batch shared by tests that need populated data."""
    from app.pipeline import orchestrator
    from app.sim.generator import generate_batch

    orchestrator.run_batch(db, generate_batch(n=40, seed=11))
    return db
