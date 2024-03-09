from os.path import isabs
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sqlalchemy.orm

TableBase = sqlalchemy.orm.declarative_base()

class DB:
    """Simple wrapper class to conveniently access a sqlite database."""

    path: str | None = None

    @staticmethod
    def create_tables() -> None:
        """Create all tables."""
        from tumcsbot.lib import get_classes_from_path
        for plugin_class in get_classes_from_path("tumcsbot.plugins", TableBase):
            plugin_class.metadata.create_all(DB.engine)
        TableBase.metadata.create_all(DB.engine)

    @staticmethod
    def set_path(path: str) -> None:
        """Set the path to the database."""
        if not isabs(path):
            raise ValueError("path to database is not absolute")
        DB.path = path
        DB.engine = create_engine('sqlite:///' + path)


    def __init__(self) -> None:
        """
        Initialize the database connection.
        """
        if not DB.path:
            raise ValueError("no path to database given")

    @contextmanager
    @staticmethod
    def session():
        SessionLocal = sessionmaker(bind=DB.engine)
        session = SessionLocal()

        try:
            yield session
        finally:
            session.close()
