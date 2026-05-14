from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_settings = get_settings()
engine = create_engine(
    _settings.sqlite_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    # Import models so SQLModel.metadata is populated before create_all.
    from app.db import models  # noqa: F401
    from app.db.seed import seed_defaults

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_defaults(session)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
