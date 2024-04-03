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
from dataclasses import asdict
from inspect import cleandoc
import json
import logging
from abc import ABC, abstractmethod
import threading
from typing import Any, Callable, Iterable, Type, final


from tumcsbot.lib.client import AsyncClient, PluginContext
from tumcsbot.lib.response import Response, StrEnum
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import DB, TableBase

from sqlalchemy import Column, String

from tumcsbot.lib.types import AsncClientMixin, CommandConfig, ZulipUser


class PluginTable(TableBase):
    __tablename__ = "Plugins"

    name = Column(String, primary_key=True)
    syntax = Column(String, nullable=True)
    description = Column(String, nullable=True)
    config = Column(String, nullable=True)


@final
class EventType(StrEnum):
    # todo: GET_USAGE = "get_usage"
    # todo: RET_USAGE = "ret_usage"
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    ZULIP = "zulip"


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
    def stop_event(cls, sender: str, dest: str | None = None) -> Event:
        return cls(sender, type=EventType.STOP, dest=dest)
    
    @classmethod
    def zulip_event(cls, sender: str, data: Any, dest: str | None = None, reply_to: str | None = None) -> Event:
        return cls(sender, type=EventType.ZULIP, data=data, dest=dest, reply_to=reply_to)
    
    @classmethod
    def restart_event(cls, sender: str, dest: str | None = None) -> Event:
        return cls(sender, type=EventType.RESTART, dest=dest)
    
    @classmethod
    def start_event(cls, sender: str, dest: str | None = None) -> Event:
        return cls(sender, type=EventType.START, dest=dest)


class Plugin(threading.Thread, ABC):
    """Abstract base class for every plugin."""

    # List of plugins which have to be loaded before this plugin.
    dependencies: list[str] = []

    # List of events this plugin is responsible for.
    events: list[EventType] = [EventType.RESTART, EventType.STOP, EventType.ZULIP]

    # List of Zulip events this plugin is responsible for.
    # See https://zulip.com/api/get-events.
    zulip_events: list[str] = []

    def __init__(self, plugin_context: PluginContext, client: AsyncClient | None = None) -> None:
        """Use _init_plugin for custom init code."""
        super().__init__(name=self.plugin_name(), daemon=True)

        # Get own logger.
        self.logger: logging.Logger = self.create_logger()
        # todo: needs fix? self.logger.handlers[0].setFormatter(fmt=logging.Formatter(LOGGING_FORMAT))
        self.logger.setLevel(plugin_context.logging_level)

        # Some declarations.
        self.plugin_context: PluginContext = plugin_context
        self.client: AsyncClient = AsyncClient(self.plugin_context) if client is None else client

        self.queue = asyncio.Queue()

        # Set the running flag.
        self.running: bool = False

        # call custom init code
        self._init_plugin()

    def _init_plugin(self) -> None:
        """Custom plugin initialization code.

        Note that this code is called from the worker thread/process.
        """
    
    def is_responsible(self, event: Event) -> bool:
        """Check if the plugin is responsible for the given Zulip event.

        Provide a minimal default implementation for such a
        responsibility check.
        """
        return event.data["type"] in self.zulip_events
    
    @abstractmethod
    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        """Process a Zulip event.

        Process the given event and return a Response or an Iterable
        consisting of Response objects.
        """
        
    
    async def run_loop(self) -> None:
        """Run the plugin loop.

        This method is called by the worker thread/process.
        """
        """Run the plugin."""
        try:
            while self.running:
                self.logger.debug("Waiting for event")
                event: Event = await self.queue.get()
                self.logger.debug("Received event")

                if event.type == EventType.STOP:
                    self.running = False
                else:
                    # default handler
                    async def handler():
                        responses = await self.handle_event(event)
                        await self.client.send_responses(responses)
                    
                    asyncio.create_task(handler())
                self.queue.task_done()
        except asyncio.CancelledError:
            self.logger.info("loop cancelled")

    @final
    def stop(self):
        self.running = False
        for task in asyncio.all_tasks(self.loop):
            task.cancel()
        self.loop.call_soon_threadsafe(self.loop.stop)

    
    @final
    def run(self):
        self.running = True
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self.run_loop())
        self.loop.run_forever()
        self.loop.close()

    @final
    def push_event(self, event: Event):
        def put():
            self.queue.put_nowait(event)
        self.loop.call_soon_threadsafe(put)

    @final
    @classmethod
    def plugin_name(cls) -> str:
        """Do not override!"""
        return cls.__module__.rsplit(".", maxsplit=1)[-1]

    def create_logger(self) -> logging.Logger:
        """Create a logger instance suitable for this plugin type."""
        return logging.getLogger(self.plugin_name())


class PluginCommandMixin(Plugin):
    """Base class tailored for interactive commands.

    This class is intendet to be inherited form **in addition** one of
    the plugin base classes in this module. (First in order.)
    It provides additional feature for command handling plugins.
    """

    # The events this command would like to receive, defaults to
    # messages.
    zulip_events = Plugin.zulip_events + ["message"]
    # todo: usage? events = Plugin.events + 

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
            cause = (": " + str(e.__cause__)) if e.__cause__ else ""
            return Response.build_message(message, str(e) + cause)

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
            AsncClientMixin.set_client(self.client)
            sender = ZulipUser(id=message["sender_id"], name=message["sender_full_name"])
            await sender
            with DB.session() as session:
                result = await func(
                    sender, session, args, opts, message
                )
            return result
        else:
            self.logger.debug(f"command not found: {command}")
            return Response.command_not_found(message)

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
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
