#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""TUM CS Bot - a generic Zulip bot.

This bot is currently especially intended for administrative tasks.
It supports several commands which can be written to the bot using
a private message or a message starting with @mentioning the bot.
"""

from __future__ import annotations
import asyncio
import difflib
import logging
import signal
from graphlib import TopologicalSorter
import sys
import threading
from typing import Any, Iterable, Type, TypeVar

from zulip import Client as ZulipClient
from tumcsbot.lib import response
from tumcsbot.lib import utils
from tumcsbot.lib.client import AsyncClient, PlublicChannels, PluginContext, Event, EventType
from tumcsbot.lib.db import DB
from tumcsbot.plugin import (
    Plugin,
    PluginTable,
    get_zulip_events_from_plugins,
)
from tumcsbot.lib.utils import LOGGING_FORMAT


T = TypeVar("T")


class TumCSBot:
    """Main Bot class.

    Use run() to run the bot.

    Arguments:
    ----------
    zuliprc       zuliprc file containing the bot's configuration
    db_path       path to the bot's database
    debug         debugging mode switch
    logfile       use LOGFILE for logging output
    """

    def __init__(
        self,
        zuliprc: str,
        db_path: str,
        debug: bool = False,
        logfile: str | None = None,
    ) -> None:
        self.events: list[str]
        self.plugins: dict[str, Plugin] = {}
        self.plugins_stopped: dict[str, Plugin] = {}
        self.restart: bool = False
        self.stopped: bool = False

        # Init logging.
        logging_level: int = logging.WARNING
        if debug:
            logging_level = logging.DEBUG

        if logfile is not None:
            logging.basicConfig(
                format=LOGGING_FORMAT,
                level=logging_level,
                filename=logfile,
            )
        else:
            logging.basicConfig(format=LOGGING_FORMAT, level=logging_level, stream=sys.stdout)

        threading.current_thread().name = "Main"

        # Init database handler.
        DB.set_path(db_path)
        # Ensure presence of Plugins table.
        DB.create_tables()

        self.loop = asyncio.get_event_loop()

        # Init the event queue. The loopback queue for the thread plugins
        # simply is the central event queue.
        self.event_queue: asyncio.Queue[Event] = asyncio.Queue()

        # get plugin context
        client = ZulipClient(config_file=zuliprc, insecure=True)
        profile = client.get_profile()
        if profile["result"] != "success":
            raise Exception("Could not get profile of bot.") from Exception(profile)

        self.plugin_context = PluginContext(
            bot_id=profile["user_id"],
            bot_mention=f"@**{profile['full_name']}**",
            zuliprc=zuliprc,
            push_loopback=self.event_queue.put,
            logging_level=logging.DEBUG if debug else logging.INFO,
        )

        # Init own Zulip client which also inits the global DB tables for all
        # Zulip client objects.
        self.client = AsyncClient(self.plugin_context)

        asyncio.run(self.init_db())

        self.event_listener: asyncio.Task[None] | None = None

        # Cleanup properly on SIGTERM and SIGINT.
        for s in [signal.SIGINT, signal.SIGTERM]:
            self.loop.add_signal_handler(s, self.stop)

        # Get the plugin classes and start the plugins in correct dependency order.
        plugin_classes: Iterable[Type[Plugin]] = utils.get_classes_from_path(
            "tumcsbot.plugins", Plugin  # type: ignore
        )
        self.start_plugins(plugin_classes, zuliprc, logging_level)

        # Get events to listen for.
        self.events = get_zulip_events_from_plugins(plugin_classes)

    def run(self) -> None:
        """Run the bot."""
        logging.info("start main loop")
        try:
            self.loop.create_task(self.run_loop())
            self.loop.run_forever()
        finally:
            logging.info("stopped main loop")
            self.loop.stop()
            self.loop.close()

    def clear_queue(self) -> None:
        """Clear the event queue."""
        while not self.event_queue.empty():
            self.event_queue.get_nowait()
            self.event_queue.task_done()

    def stop_plugins(self) -> None:
        for plugin in self.plugins.values():
            plugin.stop()
            plugin.join()
            self.plugins_stopped[plugin.plugin_name()] = plugin

        self.plugins.clear()

    async def _event_listener(self) -> None:
        logging.debug("waiting for events ...")

        async for event_data in self.client.events():
            logging.debug("received event data %s", str(event_data))
            await self.event_queue.put(
                Event(sender="_root", type=EventType.ZULIP, data=event_data)
            )
            logging.debug("waiting for events ...")

    async def init_db(self) -> None:
        """Initialize some tables of the database."""

        channel_names = await self.client.get_public_channel_names(use_db=False)
        with DB.session() as session:
            for entry in session.query(PlublicChannels).all():
                if not str(entry.ChannelName) in channel_names:
                    session.delete(entry)
            for channel in channel_names:
                if (
                    not session.query(PlublicChannels)
                    .filter_by(ChannelName=channel)
                    .first()
                ):
                    session.add(PlublicChannels(ChannelName=channel, Subscribed=0))
            session.commit()

    def stop(self) -> None:
        self.stopped = True

        # hack to stop long polling
        self.client.trigger_dummy_event()

    async def run_loop(self) -> None:
        """Run the central event queue.

        This queue does not only get the events from the event listener,
        but also loopback data from the plugins.
        """

        logging.info("start bot")

        logging.debug("start event listener, listening on events: %s", str(self.events))
        self.event_listener = self.loop.create_task(self._event_listener())

        logging.debug("start central queue")
        # todo: limit queue size
        try:
            while True:
                logging.debug("waiting for event ...")
                event = await self.event_queue.get()
                logging.debug("received event (%s) %s", id(event), str(event))

                if event.type == EventType.RESTART:
                    logging.debug("restart event received")
                    self.restart = True
                    event.type = EventType.STOP

                if self.stopped or event.type == EventType.STOP:
                    raise asyncio.CancelledError()

                if event.type == EventType.ZULIP:
                    try:
                        event.data = self.zulip_event_preprocess(event.data)
                    except Exception as exc:
                        logging.exception(exc)
                        continue

                    found_responsible = False
                    for plugin in self.plugins.values():
                        if await plugin.is_responsible(event):
                            logging.debug(
                                "push event to plugin %s", plugin.plugin_name()
                            )
                            plugin.push_event(event)
                            found_responsible = True

                    if not found_responsible and event.data["type"] != "heartbeat":
                        if command_name := event.data.get("message", {}).get(
                            "command_name", None
                        ):
                            with DB.session() as session:
                                command_names = [
                                    str(cmd.name)
                                    for cmd in session.query(PluginTable).all()
                                ]

                            matches = difflib.get_close_matches(
                                command_name, command_names, n=2
                            )

                            if matches:
                                matches = [f"`{match}`" for match in matches]
                                await self.client.send_response(
                                    response.Response.build_message(
                                        event.data["message"],
                                        f"Command not found. Did you mean {' or '.join(matches)}?",
                                    )
                                )
                            else:
                                await self.client.send_response(
                                    response.Response.build_reaction(
                                        event.data["message"], "question"
                                    )
                                )

                elif event.type not in [EventType.RESTART, EventType.STOP]:
                    logging.warn("unknown event type %s", event.type)
                
        except asyncio.exceptions.CancelledError:
            pass

        except Exception as exc:
            logging.exception(exc)

        logging.info("stoping bot")

        if self.event_listener:
            self.event_listener.cancel()

        # interrupt long polling again for cancelation to take effect
        self.client.trigger_dummy_event()

        logging.debug("clear queue: %s events dropped", self.event_queue.qsize())
        self.clear_queue()

        logging.debug("stop plugins")
        self.stop_plugins()

        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        logging.debug("tasks: %s", str(tasks))
        logging.debug("waiting for tasks to finish ...")
        await asyncio.gather(*tasks, return_exceptions=True)

        self.loop.stop()

    def start_plugins(
        self, plugin_classes: Iterable[Type[Plugin]], zuliprc: str, logging_level: int
    ) -> None:
        # First, build the correct order using the dependency information.
        plugin_class_dict: dict[str, Type[Plugin]] = {
            plugin_class.plugin_name(): plugin_class for plugin_class in plugin_classes
        }
        plugin_graph: dict[str, set[str]] = {
            plugin_class.plugin_name(): set(plugin_class.dependencies)
            for plugin_class in plugin_classes
        }

        for plugin_name in TopologicalSorter(plugin_graph).static_order():
            logging.debug("start %s", plugin_name)
            plugin_class = plugin_class_dict[plugin_name]

            plugin: Plugin = plugin_class(
                self,
                self.plugin_context,
                client=self.client,
            )

            if plugin_name in self.plugins:
                raise ValueError(f"plugin {plugin.plugin_name()} appears twice")

            self.plugins[plugin_name] = plugin
            plugin.start()

    def get_plugin_instance(self, _ty: Type[T]) -> T:
        return self.plugins[_ty.plugin_name()]  # type: ignore

    def zulip_event_preprocess(self, event: dict[str, Any]) -> dict[str, Any]:
        """Preprocess a Zulip event dictionary.

        Check if the event could be an interactive command (to be
        handled by a CommandPlugin instance).

        Check if one of the following requirements are met by the event:
          - It is a private message to the bot.
          - It is a message starting with mentioning the bot.
        The sender of the message must not be the bot itself.

        If this event may be a command, add two new fields to the
        message dict:
          command_name     The name of the command.
          command          The command without the name.
        """
        startswithping: bool = False

        if event["type"] == "message" and event["message"]["content"].startswith(
            self.client.ping
        ):
            startswithping = True

        if (
            event["type"] != "message"
            or event["message"]["sender_id"] == self.client.id
            or (event["message"]["type"] != "private" and not startswithping)
            or (
                event["message"]["type"] == "private"
                and (
                    startswithping
                    or not self.client.is_only_pm_recipient(event["message"])
                )
            )
        ):
            return event

        content: str
        message: dict[str, Any] = event["message"]

        if startswithping:
            content = message["content"][self.client.ping_len :]
        else:
            content = message["content"]

        cmd: list[str] = content.split(maxsplit=1)
        logging.debug("received command line %s", str(cmd))

        event["message"].update(
            command_name=cmd[0] if len(cmd) > 0 else "",
            command=cmd[1] if len(cmd) > 1 else "",
        )

        return event
