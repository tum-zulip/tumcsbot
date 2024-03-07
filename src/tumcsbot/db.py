from os.path import isabs
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy.ext.declarative import declarative_base

TableBase = declarative_base()

class DB:
    """Simple wrapper class to conveniently access a sqlite database."""

    path: str | None = None

    @staticmethod
    def create_tables() -> None:
        """Create all tables."""
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
        Session = sessionmaker(bind=DB.engine)
        session = Session()

        try:
            yield session
        finally:
            session.close()
