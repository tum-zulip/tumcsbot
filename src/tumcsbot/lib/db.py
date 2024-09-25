import logging

from os.path import isabs
from contextlib import contextmanager
from typing import Generator, Any
import yaml



from sqlalchemy import create_engine
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import sessionmaker
import sqlalchemy.orm

from tumcsbot.lib.utils import get_classes_from_path

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
        DB._engine = create_engine("sqlite:///" + path)

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
            raise ValueError(
                "database engine not set. Did you forget to call set_path?"
            )
        return DB._engine

    @contextmanager
    @staticmethod
    def session() -> Generator[Session, None, None]:
        SessionLocal = sessionmaker(bind=DB.engine())
        session = SessionLocal()

        try:
            session.execute(sqlalchemy.text("PRAGMA foreign_keys=ON"))
            yield session  # type: ignore
        finally:
            session.close()


async def serialize_model(
    obj: Any, exclude_integer_primary_key: bool = True, exclude_tables: list[sqlalchemy.Table] | None = None
) -> dict[str, Any]:
    """Serialize an SQLAlchemy object, excluding redundant foreign keys."""
    if not isinstance(obj, TableBase):
        raise ValueError("Object must be an SQLAlchemy model")

    if exclude_tables is None:
        exclude_tables = []

    state: sqlalchemy.orm.InstanceState = inspect(obj)  # type: ignore
    mapper: sqlalchemy.orm.Mapper = state.mapper  # type: ignore
    exclude_tables.append(obj.__class__.__table__)

    # Determine which foreign keys to skip because they are handled by relationships
    skip_keys = set()

    for c in mapper.columns:
        if c.foreign_keys:
            for fk in c.foreign_keys:
                if fk.column.table in exclude_tables:
                    skip_keys.add(c.key)
                    break
        if (
            exclude_integer_primary_key
            and c.primary_key
            and isinstance(c.type, sqlalchemy.Integer)
        ):
            skip_keys.add(c.key)

    # Serialize columns except the skipped foreign keys
    attributes = {
        c.key: getattr(obj, c.key)
        for c in mapper.column_attrs
        if c.key not in skip_keys
    }
    attributes = {k: v for k, v in attributes.items() if v is not None}
    for value in attributes.values():
        if hasattr(value, "__await__"):
            await value

    # Serialize relationships
    for rel in mapper.relationships:
        if rel.mapper.class_.__table__ in exclude_tables:
            continue  # Prevent recursion
        related_objects = getattr(obj, rel.key)
        if related_objects is not None:
            if isinstance(related_objects, list):
                models = []
                for child in related_objects:
                    model = await serialize_model(
                        child, exclude_tables=list(exclude_tables)
                    )
                    if len(model.keys()) == 1:
                        model = next(iter(model.values()))
                    models.append(model)
                attributes[rel.key] = models
            elif isinstance(related_objects, TableBase):
                attributes[rel.key] = await serialize_model(
                    related_objects, exclude_tables=list(exclude_tables)
                )

    list_attributes = {k: v for k, v in attributes.items() if isinstance(v, list)}
    dict_attributes = {k: v for k, v in attributes.items() if isinstance(v, dict)}
    attributes = {
        k: v for k, v in attributes.items() if not isinstance(v, (list, dict))
    }
    attributes.update(list_attributes)
    attributes.update(dict_attributes)
    return attributes


def deserialize_model(session: Session, model_class: type, data: dict[str, Any] | str, indent: int = 0) -> Any:
    """Deserialize data into an SQLAlchemy model, handling relationships."""
    print(f"{'     ' * indent}Deserializing: {model_class.__name__} with data: {data}")
    model = model_class()

    if not isinstance(data, dict):
        data = {data: None}

    for key, value in data.items():
        if isinstance(value, list):  # Assuming a relationship to a list of models
            rel_class = getattr(model_class, key).property.mapper.class_
            print(
                f"{'     ' * indent}Processing list relationship '{key}' ({rel_class.__name__})"
            )
            try:
                deserialized_list = [
                    deserialize_model(session, rel_class, item, indent + 1)
                    for item in value
                ]
                print(
                    f"{'     ' * indent}Setting attribute '{key}' to '{deserialized_list}'"
                )
                setattr(model, key, deserialized_list)
            except Exception as e:
                print(
                    f"{'     ' * indent}Error deserializing list relationship for key: {key} with error: {e}"
                )
                raise ValueError(f"Error deserializing list relationship {key}") from e

        elif isinstance(value, dict):  # Assuming a single related model
            rel_class = getattr(model_class, key).property.mapper.class_
            print(
                f"{'     ' * indent}Processing single relationship for key: {key} with class: ({rel_class.__name__})"
            )
            try:
                setattr(
                    model, key, deserialize_model(session, rel_class, value, indent + 1)
                )
            except Exception as e:
                print(
                    f"{'     ' * indent}Error deserializing single relationship for key: {key} with error: {e}"
                )
                raise ValueError(
                    f"Error deserializing single relationship {key}"
                ) from e

        else:
            if hasattr(model, key):
                setattr(model, key, value)
            else:
                raise ValueError(
                    f"Error deserializing: {model_class.__name__} with data: {data}"
                )
            print(f"{' ' * indent}Setting attribute '{key}' to '{value}'")
    print(f"{' ' * indent}Finished deserializing: {model_class.__name__}")
    return model


def export_yaml(obj: Any) -> str:
    """Export an SQLAlchemy object to a YAML string, with error handling."""
    try:
        serialized_data = serialize_model(obj)
        return yaml.dump(serialized_data, allow_unicode=True)
    except Exception as e:
        raise ValueError("Failed to export object to YAML") from e


def import_yaml(session: Session, model_class: type, yaml_str: str) -> Any:
    """Import a YAML string into an SQLAlchemy object, with error handling."""
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise ValueError("Failed to parse YAML") from e

    name = data.get("name")
    try:
        # Delete existing object with the same key if exists
        existing_obj = session.query(model_class).filter_by(name=name).first()
        if existing_obj:
            session.delete(existing_obj)
            session.commit()

        model = deserialize_model(session, model_class, data)
        session.add(model)
        session.commit()
    except Exception as e:
        session.rollback()
        raise e

    return model
