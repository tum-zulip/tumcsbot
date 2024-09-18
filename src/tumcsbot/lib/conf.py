from sqlalchemy import Column, String

from tumcsbot.lib.db import TableBase, DB


class ConfigTable(TableBase):  # type: ignore
    __tablename__ = "Conf"

    Key = Column(String, primary_key=True)
    Value = Column(String, nullable=False)


class Conf:

    @staticmethod
    def get(key: str) -> str | None:
        with DB.session() as session:
            result = session.query(ConfigTable).filter_by(Key=key).first()
            return str(result.Value) if result else None

    @staticmethod
    def list() -> list[tuple[str, str]]:
        with DB.session() as session:
            return [(str(t.Key), str(t.Value)) for t in session.query(ConfigTable).all()]

    @staticmethod
    def remove(key: str) -> None:
        with DB.session() as session:
            session.query(ConfigTable).filter_by(Key=key).delete()
            session.commit()

    @staticmethod
    def set(key: str, value: str) -> None:
        """Set a key.

        Note that a potential exception from the database is simply
        passed through.
        """
        with DB.session() as session:
            session.merge(ConfigTable(Key=key, Value=value))
            session.commit()

    @staticmethod
    def is_bot_owner(user_id: int) -> bool:
        """Checks whether the given user id belongs to the bot owner."""
        return Conf.get("bot_owner") == str(user_id)
