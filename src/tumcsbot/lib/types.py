from __future__ import annotations
from abc import ABC, abstractmethod
import yaml
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from queue import Empty, Queue
from threading import Thread
from typing import Any, AsyncGenerator, Callable
from sqlalchemy.types import TypeDecorator, Integer
from sqlalchemy.ext.mutable import Mutable

from tumcsbot.lib.client import AsyncClient
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.regex import Regex

from sqlalchemy.dialects.postgresql import INTEGER


class DMError(Exception):
    pass


@dataclass
class DMResponse:
    """
    Responds with a direct message to the sender.
    """

    message: str


@dataclass
class DMMessage:
    """
    Responds with a direct message to the sender.
    """

    to: ZulipUser
    message: str


@dataclass
class InlineResponse:
    """
    Responds with an inline message to the sender.
    """

    message: str


@dataclass
class ReactionResponse:
    """
    Reacts with an emote message to the sender.
    """

    emote: str


@dataclass
class PartialSuccess:
    """
    Indicates that the command was successful for a specific element dentoed by info.
    Can be used multiple times with yield.
    """

    info: str


@dataclass
class PartialError:
    """
    Indicates that the command was not successful for a specific element dentoed by info.
    Can be used multiple times with yield.
    """

    info: str


class UserNotPrivilegedException(Exception):
    pass


class ZulipUserNotFound(Exception):
    pass


class ZulipStreamNotFound(Exception):
    pass


class AsncClientMixin:
    # todo: are there probles with threads?
    _client: AsyncClient | None = None

    @classmethod
    def set_client(cls, client: AsyncClient) -> None:
        cls._client = client

    @property
    def client(self) -> AsyncClient:
        if self._client is None:
            raise ValueError("Client not set.")
        return self._client


class SqlAlchemyMixinFactory:
    @staticmethod
    def from_type(_impl):
        class Mixin(TypeDecorator, ABC):
            cache_ok = True
            impl = _impl
            cls = None

            def __init_subclass__(cls, **kwargs):
                Mixin.cls = cls

            def process_bind_param(self, value, dialect):
                if value is None:
                    return None

                return Mixin.cls.get_db_value(value)

            def process_result_value(self, value, dialect):
                if value is None:
                    return None

                return Mixin.cls(value)

            def copy_value(self, value):
                return value

            def load_dialect_impl(self, dialect):
                return dialect.type_descriptor(_impl)

            @property
            def python_type(self):
                return Mixin.cls

            @abstractmethod
            def get_db_value(self, value):
                pass

            @property
            def comparator_factory(self):
                return Mutable.Comparator

        return Mixin


class YAMLSerializableMixin(ABC):

    def __yaml__(self):
        raise NotImplementedError("Subclasses must implement __yaml__ method")

    @staticmethod
    def to_yaml(dumper, obj):
        if isinstance(obj, YAMLSerializableMixin):
            return dumper.represent_mapping(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, obj.__yaml__()
            )
        raise TypeError(f"Object of type {type(obj)} is not YAML serializable.")


yaml.add_multi_representer(YAMLSerializableMixin, YAMLSerializableMixin.to_yaml)


class ZulipUser(
    SqlAlchemyMixinFactory.from_type(Integer), AsncClientMixin, YAMLSerializableMixin
):
    """
    Inferface for Zulip users that dynamically fetches the user id and name and can be used as a type in the database.
    """

    def __init__(
        self,
        identifier: str | int | None = None,
        name: str | None = None,
        id: int | None = None,
    ) -> None:
        self._id: int | None = id
        self._name: str | None = name
        self._privileged: bool | None = None

        if isinstance(identifier, int):
            self._id = identifier

        elif isinstance(identifier, str):
            uname, uid = Regex.get_user_name(identifier, get_user_id=True)
            if uname is None:
                raise ZulipUserNotFound(
                    f"Invalid user identifier `{identifier}`, use the same format as in the Zulip UI. (`@**<username>**`)"
                )
            self._name: str | int = uname
            self._id: int | None = uid

    async def __ainit__(self):
        if self._name is None and self._id is None:
            raise ValueError("User id and name not set.")

        if self._id is None:
            result = await self.client.get_user_id_by_name(self.mention)
            if result is None:
                raise ZulipUserNotFound(
                    f"User {self.mention_silent} could be not found."
                )
            self._id = result
        if self._name is None:
            result = await self.client.get_user_by_id(self._id)
            if result["result"] != "success":
                raise ZulipUserNotFound(f"User with id {self._id} could be not found.")
            self._name = result["user"]["full_name"]

        if self._privileged is None:
            self._privileged = await self.client.user_is_privileged(self._id)

    def __await__(self):
        return self.__ainit__().__await__()

    def __yaml__(self):
        return {"name": self.name}

    @staticmethod
    def get_db_value(value):
        if isinstance(value, ZulipUser):
            result = value.id
        else:
            result = int(value)
        return result

    def __str__(self) -> str:
        return f"ZulipUser(id: {self._id}, name: {self._name})"

    @property
    def id(self) -> int:
        if self._id is None:
            raise ValueError(
                "User id not set. Did you forget to call `await` on the object?"
            )
        return self._id

    @property
    def name(self) -> str:
        if self._name is None:
            raise ValueError(
                "User name not set. Did you forget to call `await` on the object?"
            )

        return self._name

    @property
    def mention(self) -> str:
        return f"@**{self.name}**"

    @property
    def mention_silent(self) -> str:
        return f"@_**{self.name}**"

    @property
    def isPrivileged(self) -> bool:
        if self._privileged is None:
            raise ValueError(
                "Privilege not set. Did you forget to call `await` on the object?"
            )
        return self._privileged


class ZulipStream(
    SqlAlchemyMixinFactory.from_type(Integer), AsncClientMixin, YAMLSerializableMixin
):
    """
    Inferface for Zulip streams that dynamically fetches the user id and name and can be used as a type in the database.
    """

    def __init__(self, identifier: str | int | None = None) -> None:
        self._id: int | None = None
        self._name: str | None = None

        if isinstance(identifier, int):
            self._id = identifier

        elif isinstance(identifier, str):
            sname = Regex.get_stream_name(identifier)
            if sname is None:
                raise ZulipStreamNotFound(
                    f"Invalid stream identifier `{identifier}`, use the same format as in the Zulip UI. (`#**<stream>**`)"
                )
            self._name: str | int = sname

    def __str__(self) -> str:
        return f"ZulipStream(id: {self._id}, name: {self._name})"

    async def __ainit__(self):
        if self._name is None and self._id is None:
            raise ValueError("Stream id and name not set.")

        if self._id is None:
            result = await self.client.get_stream_id_by_name(self.mention)
            if result is None:
                raise ZulipStreamNotFound(f"Stream {self.mention} could be not found.")
            self._id = result
        if self._name is None:
            result = await self.client.get_stream_by_id(self._id)
            if result==None:
                raise ZulipStreamNotFound(
                    f"Stream with id {self._id} could be not found: {result}"
                )
            self._name = result["name"]

    def __await__(self):
        return self.__ainit__().__await__()

    def __yaml__(self):
        return {"name": self.name}
    
    #def __eq__(self, other):
    #    if not isinstance(other, ZulipStream):
    #        raise ValueError("Can only compare two ZulipStreams")
    #    return self.id == other.id
#
    @staticmethod
    def get_db_value(value):
        if isinstance(value, ZulipStream):
            return value.id
        return int(value)

    @property
    def id(self) -> int:
        if self._id is None:
            raise ValueError(
                "Stream id not set. Did you forget to call `await` on the object?"
            )
        return self._id

    @property
    def name(self) -> str:
        if self._name is None:
            raise ValueError(
                "Stream name not set. Did you forget to call `await` on the object?"
            )
        return self._name

    @property
    def mention(self) -> str:
        return f"#**{self.name}**"


response_type = (
    DMResponse
    | DMMessage
    | InlineResponse
    | ReactionResponse
    | PartialSuccess
    | PartialError
)

command_func_type = Callable[
    [
        Any,
        ZulipUser,
        Any,
        CommandParser.Args,
        CommandParser.Opts,
        dict[str, Any],
    ],
    AsyncGenerator[response_type, None],
]

command_decorator_type = Callable[[command_func_type], command_func_type]


class Privilege(Enum):
    USER = 1
    MODERATOR = 2
    ADMIN = 3

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Privilege):
            raise TypeError(f"cannot compare {type(self)} with {type(other)}")
        return self.value >= other.value

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Privilege):
            raise TypeError(f"cannot compare {type(self)} with {type(other)}")
        return self.value > other.value

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Privilege):
            raise TypeError(f"cannot compare {type(self)} with {type(other)}")
        return self.value <= other.value

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Privilege):
            raise TypeError(f"cannot compare {type(self)} with {type(other)}")
        return self.value < other.value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Privilege):
            raise TypeError(f"cannot compare {type(self)} with {type(other)}")
        return self.value == other.value

    @staticmethod
    def from_str(s: str | None) -> Privilege | None:
        if s is None:
            return None
        s = s.lower().split(".")[1]
        if s == "user":
            return Privilege.USER
        if s == "moderator":
            return Privilege.MODERATOR
        if s == "admin":
            return Privilege.ADMIN
        raise ValueError(f"no privilege level for {s}")


@dataclass
class ArgConfig:
    name: str
    type: Callable[[Any], Any]
    description: str | None = None
    privilege: Privilege | None = None
    greedy: bool = False
    optional: bool = False

    @property
    def syntax(self) -> str:
        lbr, rbr = ("[", "]") if self.optional else ("<", ">")
        greedy = "..." if self.greedy else ""
        return lbr + self.name + greedy + rbr

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ArgConfig":
        return ArgConfig(
            name=d["name"],
            type=d["type"],
            description=d["description"],
            privilege=Privilege.from_str(d["privilege"]),
            greedy=d["greedy"],
            optional=d["optional"],
        )


@dataclass
class OptConfig:
    opt: str
    long_opt: str | None = None
    type: Callable[[Any], Any] | None = None
    description: str | None = None
    privilege: Privilege | None = None

    @property
    def syntax(self):
        try:
            type_name = self.type.__name__
        except AttributeError:
            type_name = "arg"
        type = " <" + type_name + ">" if self.type is not None else ""
        return "[-" + self.opt + type + "]"

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "OptConfig":
        return OptConfig(
            opt=d["opt"],
            long_opt=d["long_opt"],
            type=d["type"],
            description=d["description"],
            privilege=Privilege.from_str(d["privilege"]),
        )


@dataclass
class SubCommandConfig:
    name: str | None = None
    args: list[ArgConfig] = field(default_factory=list)
    opts: list[OptConfig] = field(default_factory=list)
    privilege: Privilege = field(default_factory=lambda: Privilege.USER)
    description: str | None = None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "SubCommandConfig":
        return SubCommandConfig(
            name=d["name"],
            args=[ArgConfig.from_dict(arg) for arg in d["args"]],
            opts=[OptConfig.from_dict(opt) for opt in d["opts"]],
            privilege=Privilege.from_str(d["privilege"]),
            description=d["description"],
        )

    @property
    def syntax(self) -> str:
        if self.name is None:
            raise ValueError("Name of command is not set.")

        args = [arg.syntax for arg in self.args]
        opts = [opt.syntax for opt in self.opts]
        if len(opts + args) > 0:
            return self.name + " " + " ".join(opts + args)
        return self.name

    @property
    def short_help_msg(self) -> str:
        if self.description is None:
            return "No description available."
        return self.description


@dataclass
class CommandConfig:
    name: str | None = None
    subcommands: list[SubCommandConfig] = field(default_factory=list)
    description: str | None = None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "CommandConfig":
        return CommandConfig(
            name=d["name"],
            subcommands=[SubCommandConfig.from_dict(sub) for sub in d["subcommands"]],
            description=d["description"],
        )

    @property
    def syntax(self) -> str:
        return "\n or ".join([self.name + " " + sub.syntax for sub in self.subcommands])

    @property
    def short_help_msg(self) -> str:
        if self.description is None:
            return "No description available."
        return self.description
