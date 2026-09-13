"""SQLAlchemy engine/session for data/calendar.db. One file per the repo, per build plan §0."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from calscan.store.schema import Base

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "calendar.db"


def get_engine(db_path: Path | str = DEFAULT_DB_PATH) -> Engine:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return engine


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)
