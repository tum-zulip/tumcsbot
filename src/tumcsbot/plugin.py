#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Define (partially abstract) base classes for plugins.
Plugins may handle arbitrary events.

Classes:
--------
Event           Represent an event.
PluginContext   All information a plugin may need.
_Plugin         Abstract base class for every plugin.
PluginThread    Base class for plugins that live in a separate thread.
PluginProcess   Base class for plugins that live in a separate process.
PluginCommandMixin   Mixin class tailored for interactive commands.
"""

from __future__ import annotations
import asyncio
from dataclasses import asdict, dataclass, field
from enum import Enum
from inspect import cleandoc
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Final, Iterable, Type, final

import sqlalchemy

from tumcsbot.client import AsyncClient
from tumcsbot.lib import LOGGING_FORMAT, Regex, Response, StrEnum
from tumcsbot.command_parser import CommandParser
from tumcsbot.db import DB, TableBase
from sqlalchemy import Column, String


class PluginTable(TableBase):
    __tablename__ = "Plugins"

    name = Column(String, primary_key=True)
    syntax = Column(String, nullable=True)
    description = Column(String, nullable=True)
    config = Column(String, nullable=True)


@final
class EventType(StrEnum):
    GET_USAGE = "get_usage"
    RET_USAGE = "ret_usage"
    RELOAD = "reload"
    START = "start"
    STOP = "stop"
    ZULIP = "zulip"
    _EMPTY = "_empty"


@final
class Event:
    """Represent an event.

    Parameters:
    sender    The sender of the event. If the event requires an answer,
              the sender will also be the recipient of the answer, if
              `reply_to` is not specified.
    type      The type of event. See EventType.
    data      Additional event data.
    dest      The destination of this event. If no destination is
              specified, the event will be broadcasted.
    reply_to  If the event requires an answer, send it to the specified
              entity instead of sending it back to the original sender.
    """

    def __init__(
        self,
        sender: str,
        type: EventType,
        data: Any = None,
        dest: str | None = None,
        reply_to: str | None = None,
    ) -> None:
        self.sender: str = sender
        self.type: EventType = type
        self.data: Any = data
        self.dest: str | None = dest
        self.reply_to: str = reply_to if reply_to is not None else sender

    def __repr__(self) -> str:
        return json.dumps(
            {
                "sender": self.sender,
                "type": self.type,
                "data": str(self.data),
                "dest": self.dest,
                "reply_to": self.reply_to,
            }
        )

    @classmethod
    def _empty_event(cls, sender: str, dest: str) -> "Event":
        return cls(sender, type=EventType._EMPTY, dest=dest)

    @classmethod
    def reload_event(cls, sender: str, dest: str) -> "Event":
        return cls(sender, type=EventType.RELOAD, dest=dest)

    @classmethod
    def start_event(cls, sender: str, dest: str) -> "Event":
        return cls(sender, type=EventType.START, dest=dest)

    @classmethod
    def stop_event(cls, sender: str, dest: str) -> "Event":
        return cls(sender, type=EventType.STOP, dest=dest)


@final
class PluginContext:
    """All information a plugin may need.

    Parameters:
    -------
    zuliprc        The bot's zuliprc in case the plugin need an own
                   client instance.
    push_loopback  Method to push an event to the central event queue of
                   the bot.
    logging_level  The logging level to be used.
    """

    def __init__(
        self,
        zuliprc: str,
        push_loopback: Callable[[Event], None],
        logging_level: Any,
    ) -> None:
        self._zuliprc: Final[str] = zuliprc
        self._push_loopback: Final[Callable[[Event], None]] = push_loopback
        self._logging_level: Final[Any] = logging_level

    @property
    def logging_level(self) -> Any:
        return self._logging_level

    @property
    def push_loopback(self) -> Callable[[Event], None]:
        return self._push_loopback

    @property
    def zuliprc(self) -> str:
        return self._zuliprc


class Plugin(ABC):
    """Abstract base class for every plugin."""

    # List of plugins which have to be loaded before this plugin.
    dependencies: list[str] = []
    # List of events this plugin is responsible for.
    events: list[EventType] = [EventType.RELOAD, EventType.STOP, EventType.ZULIP]
    # List of Zulip events this plugin is responsible for.
    # See https://zulip.com/api/get-events.
    zulip_events: list[str] = []

    # todo: remove plugin_context from __init__
    def __init__(self, plugin_context: PluginContext, client: AsyncClient) -> None:
        """Use _init_plugin for custom init code."""

        # Some declarations.
        self.plugin_context: PluginContext = plugin_context
        self.client: AsyncClient = client

        # Get own logger.
        self.logger: logging.Logger = self.create_logger()
        # todo: needs fix? self.logger.handlers[0].setFormatter(fmt=logging.Formatter(LOGGING_FORMAT))
        self.logger.setLevel(plugin_context.logging_level)

        # Set the running flag.
        self.running: bool = False

        # call custom init code
        self._init_plugin()

    def _init_plugin(self) -> None:
        """Custom plugin initialization code.

        Note that this code is called from the worker thread/process.
        """

    @final
    @classmethod
    def plugin_name(cls) -> str:
        """Do not override!"""
        return cls.__module__.rsplit(".", maxsplit=1)[-1]

    def create_logger(self) -> logging.Logger:
        """Create a logger instance suitable for this plugin type."""
        return logging.getLogger(self.plugin_name())

    @abstractmethod
    async def handle_zulip_event(self, event: Event) -> Response | Iterable[Response]:
        """Process a Zulip event.

        Process the given event and return a Response or an Iterable
        consisting of Response objects.
        """
    
    @final
    async def handle_event(self, event: Event) -> None:
        """Process an event.

        Always call the default implementation of this method if you
        did not receive any custom internal event.
        """
        responses: Response | Iterable[Response] = await self.handle_zulip_event(event)
        await self.client.send_responses(responses)

    def is_responsible(self, event: Event) -> bool:
        """Check if the plugin is responsible for the given Zulip event.

        Provide a minimal default implementation for such a
        responsibility check.
        """
        return event.data["type"] in self.zulip_events



class ZulipUserNotFound(Exception):
    pass


class ZulipUser:
    # todo: are there probles with threads?
    _client: AsyncClient | None = None

    @classmethod
    def set_client(cls, client: AsyncClient) -> None:
        cls._client = client

    def __init__(self, identifier: str | str) -> None:
        self._id: int | None = None
        self._name: str | None = None

        if isinstance(identifier, int):
            self._id = identifier
            return

        if isinstance(identifier, str):
            uname = Regex.get_user_name(identifier)
            if uname is None:
                raise ZulipUserNotFound(
                    f"Invalid user identifier `{identifier}`, use the same format as in the Zulip UI. (`@**<username>**`)"
                )
            self._name: str | int = uname

    def __repr__(self) -> str:
        return f"ZulipUser({self._id}, {self._name})"

    @property
    def client(self) -> AsyncClient:
        if ZulipUser._client is None:
            raise ValueError("Client not set for ZulipUser.")
        return ZulipUser._client

    @property
    async def id(self) -> int:
        if self._id is not None:
            return self._id

        result = await self.client.get_user_id_by_name(await self.mention)
        if result is None:
            raise ZulipUserNotFound(f"User {await self.mention_silent} could be not found.")
        self._id = result
        return result

    @property
    async def name(self) -> str:
        if self._name is not None:
            return self._name

        result = await self.client.get_user_by_id(self._id)
        if result["result"] != "success":
            raise ZulipUserNotFound(f"User with id {self._id} could be not found.")
        self._name = result["user"]["full_name"]
        return self._name

    @property
    async def mention(self) -> str:
        return f"@**{await self.name}**"

    @property
    async def mention_silent(self) -> str:
        return f"@_**{await self.name}**"

    @property
    async def privileged(self) -> bool:
        return await self.client.user_is_privileged(await self.id)


class Privilege(Enum):
    USER = 1
    MODERATOR = 2
    ADMIN = 3

    def __ge__(self, other: Privilege) -> bool:
        return self.value >= other.value

    def __gt__(self, other: Privilege) -> bool:
        return self.value > other.value

    def __le__(self, other: Privilege) -> bool:
        return self.value <= other.value

    def __lt__(self, other: Privilege) -> bool:
        return self.value < other.value

    def __eq__(self, other: Privilege) -> bool:
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
    privilege: Privilege = Privilege.USER
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


class PluginCommandMixin(Plugin):
    """Base class tailored for interactive commands.

    This class is intendet to be inherited form **in addition** one of
    the plugin base classes in this module. (First in order.)
    It provides additional feature for command handling plugins.
    """

    # The events this command would like to receive, defaults to
    # messages.
    zulip_events = Plugin.zulip_events + ["message"]
    events = Plugin.events + [EventType.GET_USAGE]

    # The command parser.
    _tumcs_bot_command_parser: CommandParser = CommandParser()
    # The command dictionary. Maps command names to their description and syntax.
    _tumcs_bot_commands: CommandConfig = CommandConfig()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        syntax = getattr(self, "syntax", None)
        description = cleandoc(self.__doc__) if self.__doc__ else None
        description = getattr(self, "description", description)

        self._tumcs_bot_commands.name = self.plugin_name()
        self._tumcs_bot_commands.description = description

        with DB.session() as session:
            # todo: handle custom syntax
            session.merge(
                PluginTable(
                    name=self.plugin_name(),
                    syntax=syntax,
                    description=description,
                    config=json.dumps(asdict(self._tumcs_bot_commands), default=str),
                )
            )
            session.commit()

    @final
    def get_usage(self) -> tuple[str, str, str]:
        """Get own documentation to help users use this command.

        Return a tuple containing:
        - the name of the command,
        - the syntax of the command, and
        - its description.
        Example:
            ('command', 'command [OPTION]... [FILE]...',
            'this command does a lot of interesting stuff...')
        The description may contain Zulip-compatible markdown.
        Newlines in the description will be removed.
        The syntax string is formatted as code (using backticks)
        automatically.
        """
        return (self.plugin_name(), self.syntax, self.description)


    async def handle_message(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        """Process message.

        Process the given message and return a Response or an Iterable
        consisting of Response objects.
        """
        result: tuple[str, CommandParser.Opts, CommandParser.Args] | None
        command_parser: CommandParser = self.__class__._tumcs_bot_command_parser
        # Get command and parameters.

        try:
            result = command_parser.parse(message["command"])
        except CommandParser.IllegalCommandParserState as e:
            self.logger.exception(e)
            return Response.build_message(message, str(e))

        if result is None:
            return Response.command_not_found(message)
        command, opts, args = result

        if command in command_parser.commands:
            func: Callable[
                [
                    ZulipUser,
                    Any,
                    CommandParser.Args,
                    CommandParser.Opts,
                    dict[str, Any],
                ],
                Response | Iterable[Response],
            ] = getattr(self, command)
            self.logger.debug(f"executing subcommand: {command}")
            self.logger.debug(f"args: {args}")
            self.logger.debug(f"opts: {opts}")
            with DB.session() as session:
                try:
                    result = await func(
                        ZulipUser(message["sender_id"]), session, args, opts, message
                    )
                except sqlalchemy.exc.IntegrityError as e:
                    result = Response.build_message(
                        message, f"Database error: {str(e)}"
                    )
            return result
        else:
            self.logger.debug(f"command not found: {command}")
            return Response.command_not_found(message)

    async def handle_zulip_event(self, event: Event) -> Response | Iterable[Response]:
        """Defaults to assume event to be a message event.

        Overwrite if necessary!
        """
        return await self.handle_message(event.data["message"])

    def is_responsible(self, event: Event) -> bool:
        """A default implementation for command plugins.

        May need to be overriden to meet more enhanced requirements.
        """
        return (
            super().is_responsible(event)
            and "message" in event.data
            and "command_name" in event.data["message"]
            and event.data["message"]["command_name"] == self.plugin_name()
        )


def get_zulip_events_from_plugins(
    plugins: Iterable[Plugin] | Iterable[Type[Plugin]],
) -> list[str]:
    """Get all Zulip events to listen to from the plugins.

    Every plugin decides on its own which events it likes to receive.
    The plugins passed to this function may be classes or instances.
    """
    events: set[str] = set()
    for plugin in plugins:
        events.update(plugin.zulip_events)
    return list(events)
