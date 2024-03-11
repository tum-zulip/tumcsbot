from sqlalchemy import Column, String

from tumcsbot.db import TableBase, DB

class ConfigTable(TableBase):
    __tablename__ = "Conf"

    Key = Column(String, primary_key=True)
    Value = Column(String, nullable=False)


class Conf:

    @staticmethod
    def get(key: str) -> str | None:
        with DB.session() as session:
            result: str | None = session.query(ConfigTable).filter_by(Key=key).first()
            return result.Value if result else None
        
    @staticmethod
    def list() -> list[tuple[str, str]]:
        with DB.session() as session:
            return [(t.Key, t.Value) for t in session.query(ConfigTable).all()]
    
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
    def is_bot_owner(user_id: int, db: DB | None = None) -> bool:
        """Checks whether the given user id belongs to the bot owner."""
        conf: Conf = Conf(db=db)
        return conf.get("bot_owner") == str(user_id)