#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import asyncio
from inspect import cleandoc
import logging
import time
from datetime import timedelta
from typing import Any, Iterable

from sqlalchemy import Column
from tumcsbot.lib.conf import Conf
from tumcsbot.lib.db import DB, TableBase
from tumcsbot.lib.response import Response
from tumcsbot.lib.types import ZulipUser, ZulipChannel
from tumcsbot.plugin import Plugin
from tumcsbot.plugins.userinput import UserInput
from tumcsbot.lib.client import Event


class GarbageCollectorIgnoreChannelsTable(TableBase):  # type: ignore
    __tablename__ = "GarbageCollectorIgnoreChannels"

    Channel = Column(ZulipChannel, unique=True, primary_key=True, autoincrement=False)  # type: ignore


class GarbageCollector(Plugin):
    ASK_TO_KEEP_CHANNELS = cleandoc(
        """
                                    Hi {},
                                    The channel(s) {} has/have been inactive for a while and should be deleted if it/they is/are not needed anymore.
                                    This helps to keep the channel list clean and organized.
                                    If you want to keep the channel, please react with :floppy_disk: to this message.
                                    You can also react with :trash_can: to delete the channel.
                                    If you do not respond within {}, i will keep the channel(s) for now.
                                   
                                    If you have any questions, feel free to ask {}.
                                    Have a nice day! 
                                    """
    )

    def _init_plugin(self) -> None:
        # run the garbage collector periodically
        self._garbage_collector_task: asyncio.Task[Any] | None = None
        self.pending_garbage_collections: list[int] = []
        self.pending_garbage_collection_tasks: list[asyncio.Task[Any]] = []

    async def is_responsible(self, event: Event) -> bool:
        if event.data["type"] == "heartbeat":
            return True
        return False

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        if self._garbage_collector_task is None:
            self._garbage_collector_task = asyncio.create_task(
                self._garbage_collect_loop()
            )
        return []

    def _getTimings(self) -> tuple[bool, int, int]:
        threshhold = Conf.get("garbage_collector_no_activity_threshold_seconds")
        time_to_responde = Conf.get("garbage_collector_time_to_responde_seconds")
        if threshhold is None:
            logging.error("garbage_collector_no_activity_threshold_seconds is not set")
            return True, 0, 0

        if time_to_responde is None:
            logging.error("garbage_collector_time_to_responde_seconds is not set")
            return True, 0, 0

        return False, int(threshhold), int(time_to_responde)

    async def _garbage_collect_loop(self) -> None:
        try:
            while True:
                channels = await self.client.get_channels()
                if channels["result"] != "success":
                    logging.error("could not get channels")
                    return

                err, threshhold, time_to_responde = self._getTimings()
                if err:
                    return

                ZulipChannel.set_client(self.client)
                ZulipUser.set_client(self.client)

                bot_owner = Conf.get("bot_owner")
                if bot_owner is None:
                    logging.error("bot_owner is not set")
                    return

                bot_owner = ZulipUser(int(bot_owner))
                await bot_owner

                channels_to_collect: dict[int, ZulipChannel] = {}
                channel_admin_members: dict[int, frozenset[ZulipUser]] = {}
                admins = await self._get_admin_users()

                with DB.session() as session:
                    ignore = session.query(
                        GarbageCollectorIgnoreChannelsTable.Channel
                    ).all()
                    ignore = [s.Channel.id for s in ignore]

                for channel in channels["streams"]:
                    if channel["stream_id"] in ignore:
                        continue

                    if await self._channel_requires_collection(channel, threshhold):
                        channel = ZulipChannel(channel["stream_id"])
                        await channel
                        channels_to_collect[channel.id] = channel
                        admin_members = await self._get_admin_members(admins, channel)
                        admin_members.append(bot_owner)
                        channel_admin_members[channel.id] = frozenset(admin_members)

                    # wait before starting the next task to avoid rate limiting
                    await asyncio.sleep(2)

                channels_by_admins: dict[
                    frozenset[int], tuple[list[ZulipChannel], list[ZulipUser]]
                ] = {}

                keys = set(
                    frozenset([u.id for u in users])
                    for users in channel_admin_members.values()
                )
                for key in keys:
                    zchls = [
                        channels_to_collect[channel_id]
                        for channel_id, admins in channel_admin_members.items()
                        if frozenset(u.id for u in admins) == key
                    ]
                    admins = list(channel_admin_members[zchls[0].id])
                    channels_by_admins[key] = (zchls, admins)

                gc_tasks = []
                for zchls, admins in channels_by_admins.values():
                    # wait to avoid rate limiting
                    await asyncio.sleep(30)
                    gc_tasks.append(
                        self._garbage_collect(
                            zchls, bot_owner, admins, time_to_responde
                        )
                    )

                # await all tasks
                await asyncio.gather(*gc_tasks)

                await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.exception(e)

            # wait before starting the next task to avoid rate limiting
            await asyncio.sleep(60)
        finally:
            self._garbage_collector_task = None

    async def _channel_requires_collection(
        self, channel: dict[str, Any], threshhold: int
    ) -> bool:
        if channel["stream_id"] in self.pending_garbage_collections:
            return False

        # get last message sent in the channel
        messages = await self.client.get_messages(
            {
                "anchor": "newest",
                "num_before": 1,
                "num_after": 0,
                "narrow": [{"operator": "stream", "operand": channel["name"]}],
            }
        )
        if messages["result"] != "success":
            logging.error("could not get messages for channel %s", channel["name"])
            return False

        if len(messages["messages"]) == 0:
            # check channel created date
            last_modified_date = channel["date_created"]
        else:
            last_message = messages["messages"][0]
            last_modified_date = last_message["timestamp"]

        if last_modified_date + threshhold < time.time():
            return True

        return False

    async def _get_admin_users(self) -> list[ZulipUser]:
        response = await self.client.call_endpoint(
            url="users",
            method="GET",
            request={},
        )
        if response["result"] != "success":
            logging.error("could not get users")
            return []

        users = response["members"]
        admin_users = [
            ZulipUser(ID=user["user_id"], name=user["full_name"])
            for user in users
            if user["is_admin"]
        ]

        return admin_users

    async def _get_admin_members(
        self, admins: list[ZulipUser], channel: ZulipChannel
    ) -> list[ZulipUser]:
        url = f"streams/{channel.id}/members"
        response = await self.client.call_endpoint(
            url=url,
            method="GET",
            request={},
        )

        if response["result"] != "success":
            logging.error("could not get subscribers for channel %s", channel.mention)
            return []

        admins_in_channel = [
            user for user in admins if user.id in response["subscribers"]
        ]

        return admins_in_channel

    async def _garbage_collect(
        self,
        channels: list[ZulipChannel],
        bot_owner: ZulipUser,
        admins_in_channels: list[ZulipUser],
        time_to_responde: int,
    ) -> None:
        for channel in channels:
            self.pending_garbage_collections.append(channel.id)

        content = GarbageCollector.ASK_TO_KEEP_CHANNELS.format(
            ", ".join(
                [
                    user.mention
                    for user in admins_in_channels
                    if user.id != self.client.id
                ]
            ),
            ", ".join([channel.mention for channel in channels]),
            format_time(time_to_responde),
            bot_owner.mention,
        )
        response = await self.client.send_response(
            Response.build_message(
                None,
                content=content,
                to=[u.id for u in admins_in_channels],
                msg_type="private",
            )
        )

        if response["result"] != "success":
            logging.error("could not send message to channel admins: %s", channel.name)
            return

        m_id = response["id"]

        # wait for user client to process the message
        await asyncio.sleep(0.2)
        result, _ = await UserInput.choose(
            self.client, m_id, ["trash_can", "floppy_disk"], timeout=time_to_responde
        )

        if result is not None and result == "trash_can":
            for channel in channels:
                # wait before starting the next task to avoid rate limiting
                await asyncio.sleep(10)

                self.pending_garbage_collections.remove(channel.id)
                logging.info("deleting channel %s", channel.name)
                await self.client.call_endpoint(
                    url=f"streams/{channel.id}",
                    method="DELETE",
                    request={},
                )
                if response["result"] != "success":
                    logging.error("could not delete channel %s", channel.name)

                await self.client.send_response(
                    Response.build_message(
                        None,
                        content=f"The channel {channel.mention} has been deleted.",
                        to=[u.id for u in admins_in_channels],
                        msg_type="private",
                    )
                )

        else:
            for channel in channels:
                # wait before starting the next task to avoid rate limiting
                await asyncio.sleep(10)

                self.pending_garbage_collections.remove(channel.id)
                logging.info("keeping channel %s", channel.name)

                # send message to channel so that is not deleted in the future
                content = "Although the channel has been inactive, it will not be deleted for now.\nHave a nice day!"
                response = await self.client.send_response(
                    Response.build_message(
                        None,
                        content=content,
                        to=channel.id,
                        msg_type="channel",
                        subject="Keep Channel",
                    )
                )

                if response["result"] != "success":
                    logging.error("could not send message to channel %s", channel.name)

        await self.client.delete_message(m_id)

def format_time(seconds:int) -> str:
    days, seconds = divmod(seconds, 86400) 
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    
    formatted_time = []
    if days >= 7:
        weeks = days // 7
        formatted_time.append(f"{int(weeks)} week{'s' if weeks > 1 else ''}")
        days %= 7
    if days > 0:
        formatted_time.append(f"{int(days)} day{'s' if days > 1 else ''}")
    if hours > 0:
        formatted_time.append(f"{int(hours)} hour{'s' if hours > 1 else ''}")
    if minutes > 0:
        formatted_time.append(f"{int(minutes)} minute{'s' if minutes > 1 else ''}")
    if seconds > 0 or len(formatted_time) == 0:
        formatted_time.append(f"{int(seconds)} second{'s' if seconds > 1 else ''}")

    return ", ".join(formatted_time)
