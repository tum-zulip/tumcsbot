#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Keep the bot subscribed to all public streams.

Reason:
As the 'all_public_streams' parameter of the event API [1] does not
seem to work properly, we need a work-around in order to be able to
receive events for all public streams.

[1] https://zulip.com/api/register-queue#parameter-all_public_streams
"""

import asyncio
from inspect import cleandoc
import logging
import time
from typing import Any, Iterable

from sqlalchemy import Column
from tumcsbot.lib.conf import Conf
from tumcsbot.lib.db import DB, TableBase
from tumcsbot.lib.response import Response
from tumcsbot.lib.types import ZulipUser, ZulipStream
from tumcsbot.plugin import Event,Plugin
from tumcsbot.plugins.userinput import UserInput


class GarbageCollectorIgnoreStreamsTable(TableBase):
    __tablename__ = "GarbageCollectorIgnoreStreams"

    Stream = Column(ZulipStream, unique=True, primary_key=True, autoincrement=False)

    


class GarbageCollector(Plugin):
    """Keep the bot subscribed to all public streams."""

    ASK_TO_KEEP_STREAMS = cleandoc( """
                                    Hi {},
                                    The stream(s) {} has/have been inactive for a while and should be deleted if it/they is/are not needed anymore.
                                    This helps to keep the stream list clean and organized.
                                    If you want to keep the stream, please react with :floppy_disk: to this message.
                                    You can also react with :trash_can: to delete the stream.
                                    If you do not respond within a week, the stream will be deleted. 
                                   
                                    If you have any questions, feel free to ask the {}.
                                    Have a nice day! 
                                    """)
    
    def _init_plugin(self) -> None:
        # asyncio.run(self._handle_stream(stream.StreamName, False))
        
        #run the garbage collector periodically
        self._garbage_collector_task = None
        self.pending_garbage_collections: list[int] = []
        self.pending_garbage_collection_tasks: list[asyncio.Task] = []

                    
    async def is_responsible(self, event: Event) -> bool:
        return event.data["type"] == "heartbeat"
    

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        if self._garbage_collector_task is None:
            self._garbage_collector_task = asyncio.create_task(self._garbage_collect_loop())
        return []


    async def _garbage_collect_loop(self):
        try:
            while True:
                strams = await self.client.get_streams()
                if strams["result"] != "success":
                    logging.error("could not get streams")
                    return
                
                threshhold = Conf.get("garbage_collector_no_activity_threshold_seconds")
                time_to_responde = Conf.get("garbage_collector_time_to_responde_seconds")
                if threshhold is None:
                    logging.error("garbage_collector_no_activity_threshold_seconds is not set")
                    return
                
                if time_to_responde is None:
                    logging.error("garbage_collector_time_to_responde_seconds is not set")
                    return
                
                threshhold = int(threshhold)
                time_to_responde = int(time_to_responde)

                ZulipStream.set_client(self.client)
                ZulipUser.set_client(self.client)

                bot_owner = Conf.get("bot_owner")
                if bot_owner is None:
                    logging.error("bot_owner is not set")
                    return
                else:
                    bot_owner = ZulipUser(int(bot_owner))
                    await bot_owner


                streams_to_collect: dict[int, ZulipStream] = {}
                stream_admin_members: dict[int, frozenset[ZulipUser]] = {}
                admins = await self._get_admin_users()

                with DB.session() as session:
                    ignore = session.query(GarbageCollectorIgnoreStreamsTable.Stream).all()
                    ignore = [s.Stream.id for s in ignore]

                for stream in strams["streams"]:
                    if stream["stream_id"] in ignore:
                        continue
                    
                    if await self._stream_requires_collection(stream, threshhold):
                        stream = ZulipStream(stream["stream_id"])
                        await stream
                        streams_to_collect[stream.id] = stream
                        admin_members = await self._get_admin_members(admins, stream)
                        admin_members.append(bot_owner)
                        stream_admin_members[stream.id] =  frozenset(admin_members)

                    # wait before starting the next task to avoid rate limiting
                    await asyncio.sleep(2)
                    
                
                streams_by_admins: dict[frozenset[int], tuple[list[ZulipStream], list[ZulipUser]]] = {}

                keys = set(frozenset([u.id for u in users]) for users in stream_admin_members.values())
                for key in keys:
                    streams = [streams_to_collect[stream_id] for stream_id, admins in stream_admin_members.items() if frozenset(u.id for u in admins) == key]
                    admins = stream_admin_members[streams[0].id]
                    streams_by_admins[key] = (streams, admins)

                gc_tasks = []
                for streams, admins in streams_by_admins.values():
                    # wait to avoid rate limiting
                    await asyncio.sleep(30)
                    gc_tasks.append(self._garbage_collect(streams, bot_owner, admins, time_to_responde))


                
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


    async def _stream_requires_collection(self, stream: dict, threshhold: int) -> bool:
        if stream["stream_id"] in self.pending_garbage_collections:
            return False

        # get last message sent in the stream
        messages = await self.client.get_messages({ "anchor": "newest", "num_before": 1, "num_after": 0, "narrow": [{"operator": "stream", "operand": stream["name"]}]})
        if messages["result"] != "success":
            logging.error("could not get messages for stream %s", stream["name"])
            return
        
        if len(messages["messages"]) == 0:
            # check stream created date
            last_modified_date = stream["date_created"]
        else:
            last_message = messages["messages"][0]
            last_modified_date = last_message["timestamp"]

        if last_modified_date + threshhold < time.time():
            return True
        
    
    async def _get_admin_users(self) -> list[ZulipUser]:
        response = await self.client.call_endpoint(url="users", method="GET", request={},)
        if response["result"] != "success":
            logging.error("could not get users")
            return
        
        users = response["members"]
        admin_users = [ZulipUser(id=user["user_id"], name=user["full_name"]) for user in users if user["is_admin"]]

        return admin_users


    async def _get_admin_members(self, admins: list[ZulipUser], stream: ZulipStream) -> list[ZulipUser]:
        url = "streams/%d/members" % (stream.id,)
        response = await self.client.call_endpoint(url=url,method="GET",request={},)

        if response["result"] != "success":
            logging.error("could not get subscribers for stream %s", stream.mention)
            return
    

        admins_in_stream = [user for user in admins if user.id in response["subscribers"]]

        return admins_in_stream


    async def _garbage_collect(self, streams: list[ZulipStream], bot_owner: ZulipUser, admins_in_streams: list[ZulipUser], time_to_responde: int) -> None:
        for stream in streams:
            self.pending_garbage_collections.append(stream.id)

        content = GarbageCollector.ASK_TO_KEEP_STREAMS.format(', '.join([user.mention for user in admins_in_streams if user.id != self.client.id]), ', '.join([stream.mention for stream in streams]), bot_owner.mention)
        response = await self.client.send_response(Response.build_message(None, content=content, to=[u.id for u in admins_in_streams], msg_type="private"))

        if response["result"] != "success":
            logging.error("could not send message to stream admins: %s", stream.name)
            return
        
        m_id = response["id"]

        # wait for user client to process the message
        await asyncio.sleep(.2)
        result, _ = await UserInput.choose(self.client, m_id, ["trash_can", "floppy_disk"], timeout=time_to_responde)

        if result is not None and result == "trash_can":
            for stream in streams:
                # wait before starting the next task to avoid rate limiting
                await asyncio.sleep(10)

                self.pending_garbage_collections.remove(stream.id)
                logging.info("deleting stream %s", stream.name)
                await self.client.call_endpoint(url="streams/%d" % (stream.id,), method="DELETE", request={},)
                if response["result"] != "success":
                    logging.error("could not delete stream %s", stream.name)

                await self.client.send_response(Response.build_message(None, content=f"The stream {stream.mention} has been deleted.", to=[u.id for u in admins_in_streams], msg_type="private"))

        else:
            for stream in streams:
                # wait before starting the next task to avoid rate limiting
                await asyncio.sleep(10)

                self.pending_garbage_collections.remove(stream.id)
                logging.info("keeping stream %s", stream.name)

                # send message to stream so that is not deleted in the future
                content = "Although the stream was inactive, it will not be deleted for now.\nHave a nice day!"
                response = await self.client.send_response(Response.build_message(None, content=content, to=stream.id, msg_type="stream", subject="Keep Stream"))

                if response["result"] != "success":
                    logging.error("could not send message to stream %s", stream.name)

        await self.client.delete_message(m_id)



