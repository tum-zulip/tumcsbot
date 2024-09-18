from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Callable, Coroutine, cast, Generator

from dataclasses import dataclass, field
from enum import Enum

import yaml

from sqlalchemy.types import TypeDecorator, Integer
from sqlalchemy.ext.mutable import Mutable
import sqlalchemy

from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.regex import Regex
from tumcsbot.lib.client import AsyncClient
from tumcsbot.lib.response import Response

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


class ZulipChannelNotFound(Exception):
    pass


class AsyncClientMixin:
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
    def from_type(_impl: type) -> type:
        class Mixin(TypeDecorator, ABC):  # type: ignore
            cache_ok = True
            impl = _impl
            cls: type | None = None

            def __init_subclass__(cls: type, **kwargs: Any) -> None:
                Mixin.cls = cls

            def process_bind_param(self, value: Any, dialect: Any) -> Any:
                if value is None:
                    return None

                if Mixin.cls is None:
                    raise ValueError("cls not set.")

                get_db_value = getattr(Mixin.cls, "get_db_value", None)
                if get_db_value is None:
                    raise ValueError("get_db_value not set.")

                return get_db_value(value)

            def process_result_value(self, value: Any, dialect: Any) -> Any:
                if value is None:
                    return None

                if Mixin.cls is None:
                    raise ValueError("cls not set.")

                return Mixin.cls(value)

            def copy_value(self, value: Any) -> Any:
                return value

            def load_dialect_impl(self, dialect: Any) -> Any:
                return dialect.type_descriptor(_impl)

            @property
            def python_type(self) -> type:
                if Mixin.cls is None:
                    raise ValueError("cls not set.")
                return Mixin.cls

            @staticmethod
            @abstractmethod
            def get_db_value(value: Any) -> Any:
                pass

            @property
            def comparator_factory(self) -> Any:
                return Mutable.Comparator  # type: ignore

            @comparator_factory.setter
            def comparator_factory(self, value: type) -> None:
                raise NotImplementedError("comparator_factory is read-only")

        return Mixin


class YAMLSerializableMixin(ABC):

    def __yaml__(self) -> dict[str, Any]:
        raise NotImplementedError("Subclasses must implement __yaml__ method")

    @staticmethod
    def to_yaml(dumper: yaml.Dumper, obj: Any) -> Any:
        if isinstance(obj, YAMLSerializableMixin):
            return dumper.represent_mapping(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, obj.__yaml__()
            )
        raise TypeError(f"Object of type {type(obj)} is not YAML serializable.")


yaml.add_multi_representer(YAMLSerializableMixin, YAMLSerializableMixin.to_yaml)


class ZulipUser(
    SqlAlchemyMixinFactory.from_type(Integer), AsyncClientMixin, YAMLSerializableMixin  # type: ignore
):
    """
    Inferface for Zulip users that dynamically fetches the user ID and name and can be used as a type in the database.
    """

    cache_ok = True

    def __init__(
        self,
        identifier: str | int | None = None,
        name: str | None = None,
        ID: int | None = None,
    ) -> None:
        super().__init__()
        self._id: int | None = ID
        self._name: str | None = name
        self._privileged: bool | None = None

        if isinstance(identifier, int):
            self._id = identifier

        elif isinstance(identifier, str):
            mapping = Regex.get_user_name(identifier, get_user_id=True)
            if mapping is None:
                raise ZulipUserNotFound(
                    f"Invalid user identifier `{identifier}`, use the same format as in the Zulip UI. (`@**<username>**`)"
                )

            uname, uid = cast(tuple[str, int], mapping)
            self._name = uname
            self._id = uid

    async def __ainit__(self) -> None:
        if self._name is None and self._id is None:
            raise ValueError("User ID and name not set.")

        if self._id is None:
            uid = await self.client.get_user_id_by_name(self.mention)
            if uid is None:
                raise ZulipUserNotFound(
                    f"User {self.mention_silent} could be not found."
                )
            self._id = uid

        if self._name is None:
            result = await self.client.get_user_by_id(self._id)
            if result["result"] != "success":
                raise ZulipUserNotFound(f"User with ID {self._id} could be not found.")
            self._name = result["user"]["full_name"]

        if self._privileged is None:
            self._privileged = await self.client.user_is_privileged(self._id)

        return None

    def __await__(self) -> Generator[Any, None, None]:
        return self.__ainit__().__await__()

    def __yaml__(self) -> dict[str, Any]:
        return {"name": self.name}

    @staticmethod
    def get_db_value(value: Any) -> int:
        if isinstance(value, ZulipUser):
            result = value.id
        else:
            result = int(value)
        return result

    def __str__(self) -> str:
        return f"ZulipUser(ID: {self._id}, name: {self._name})"

    @property
    def id(self) -> int:
        if self._id is None:
            raise ValueError(
                "User ID not set. Did you forget to call `await` on the object?"
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


class ZulipChannel(
    SqlAlchemyMixinFactory.from_type(Integer), AsyncClientMixin, YAMLSerializableMixin  # type: ignore
):
    """
    Inferface for Zulip channels that dynamically fetches the user ID and name and can be used as a type in the database.
    """

    def __init__(self, identifier: str | int | None = None) -> None:
        super().__init__()

        self._id: int | None = None
        self._name: str | None = None

        if isinstance(identifier, int):
            self._id = identifier

        elif isinstance(identifier, str):
            sname = Regex.get_channel_name(identifier)
            if sname is None:
                raise ZulipChannelNotFound(
                    f"Invalid channel identifier `{identifier}`, use the same format as in the Zulip UI. (`#**<channel>**`)"
                )
            self._name = sname

    def __str__(self) -> str:
        return f"ZulipChannel(ID: {self._id}, name: {self._name})"

    async def __ainit__(self) -> None:
        if self._name is None and self._id is None:
            raise ValueError("Channel ID and name not set.")

        if self._id is None:
            id_result = await self.client.get_channel_id_by_name(self.mention)
            if id_result is None:
                raise ZulipChannelNotFound(
                    f"Channel {self.mention} could be not found."
                )
            self._id = id_result
        if self._name is None:
            result = await self.client.get_channel_by_id(self._id)
            if result is None:
                raise ZulipChannelNotFound(
                    f"Channel with ID {self._id} could be not found: {result}"
                )
            self._name = result["name"]

        return None

    async def __await__(self) -> Coroutine[None, None, None]:
        for e in self.__ainit__().__await__():
            await e
        return self

    def __yaml__(self) -> dict[str, Any]:
        return {"name": self.name}

    @staticmethod
    def get_db_value(value: Any) -> int:
        if isinstance(value, ZulipChannel):
            return value.id
        return int(value)

    @property
    def id(self) -> int:
        if self._id is None:
            raise ValueError(
                "Channel ID not set. Did you forget to call `await` on the object?"
            )
        return self._id

    @property
    def name(self) -> str:
        if self._name is None:
            raise ValueError(
                "Channel name not set. Did you forget to call `await` on the object?"
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
    | Response
)

arg_type = (
    Callable[[Any], Any]
    | sqlalchemy.Column[Any]
    | sqlalchemy.orm.InstrumentedAttribute[Any]
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
    ty: Callable[[Any], Any]
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
            ty=d["ty"],
            description=d["description"],
            privilege=Privilege.from_str(d["privilege"]),
            greedy=d["greedy"],
            optional=d["optional"],
        )


@dataclass
class OptConfig:
    opt: str
    long_opt: str | None = None
    ty: Callable[[Any], Any] | None = None
    description: str | None = None
    privilege: Privilege | None = None

    @property
    def syntax(self) -> str:
        if self.ty is None:
            raise ValueError("Type of option not set.")
        try:
            type_name = self.ty.__name__
        except AttributeError:
            type_name = "arg"
        ty = " <" + type_name + ">" if self.ty is not None else ""
        return "[-" + self.opt + ty + "]"

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "OptConfig":
        return OptConfig(
            opt=d["opt"],
            long_opt=d["long_opt"],
            ty=d["ty"],
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
            privilege=Privilege.from_str(d["privilege"]) or Privilege.USER,
            description=d["description"],
        )

    def syntax_for(self, privilege: Privilege) -> str:
        if self.name is None:
            raise ValueError("Name of command is not set.")

        args = [
            arg.syntax
            for arg in self.args
            if not arg.privilege or arg.privilege <= privilege
        ]
        opts = [
            opt.syntax
            for opt in self.opts
            if not opt.privilege or opt.privilege <= privilege
        ]

        if len(opts + args) > 0:
            return self.name + " " + " ".join(opts + args)
        return self.name

    @property
    def syntax(self) -> str:
        return self.syntax_for(Privilege.ADMIN)

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

    def syntax_for(self, privilege: Privilege) -> str:
        if self.name is None:
            raise ValueError("Name of command is not set.")

        return "\n or ".join(
            [
                self.name + " " + sub.syntax_for(privilege)
                for sub in self.subcommands
                if not sub.privilege or sub.privilege <= privilege
            ]
        )

    @property
    def syntax(self) -> str:
        return self.syntax_for(Privilege.ADMIN)

    @property
    def short_help_msg(self) -> str:
        if self.description is None:
            return "No description available."
        return self.description
