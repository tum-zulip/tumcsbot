from os.path import isabs
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sqlalchemy.orm

from tumcsbot.lib import get_classes_from_path

TableBase = sqlalchemy.orm.declarative_base()

class Session(sqlalchemy.orm.Session):
    pass

class DB:
    """Simple wrapper class to conveniently access a sqlite database."""

    _path: str | None = None
    _engine: sqlalchemy.engine.Engine | None = None

    @staticmethod
    def create_tables() -> None:
        """Create all tables."""
        for plugin_class in get_classes_from_path("tumcsbot.plugins", TableBase):
            plugin_class.metadata.create_all(DB.engine())
        TableBase.metadata.create_all(DB.engine())

    @staticmethod
    def set_path(path: str) -> None:
        """Set the path to the database."""
        if not isabs(path):
            raise ValueError("path to database is not absolute")
        DB._path = path
        DB._engine = create_engine('sqlite:///' + path)

    @staticmethod
    def path() -> str:
        """Get the path to the database."""
        if not DB._path:
            raise ValueError("database path not set. Did you forget to call set_path?")
        return DB._path
    
    @staticmethod
    def engine() -> sqlalchemy.engine.Engine:
        """Get the database engine."""
        if not DB._engine:
            raise ValueError("database engine not set. Did you forget to call set_path?")
        return DB._engine

    @contextmanager
    @staticmethod
    def session() -> Generator[Session, None, None]:
        SessionLocal = sessionmaker(bind=DB.engine())
        session = SessionLocal()

        try:
            yield session # type: ignore
        finally:
            session.close()
