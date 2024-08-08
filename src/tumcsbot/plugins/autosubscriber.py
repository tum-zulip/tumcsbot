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
from typing import Iterable

from tumcsbot.lib.response import Response
from tumcsbot.plugin import Event,Plugin
from tumcsbot.lib.db import DB

from tumcsbot.tumcsbot import PlublicStreams


class AutoSubscriber(Plugin):
    """Keep the bot subscribed to all public streams."""
    
    def _init_plugin(self) -> None:
        self._db: DB = DB()
        # Ensure that we are subscribed to all existing streams.
        with DB.session() as session:
            streams = session.query(PlublicStreams).all()
            for stream in streams:
                if stream.Subscribed != 1:
                    asyncio.run(self._handle_stream(stream.StreamName, False))

    async def is_responsible(self, event: Event) -> bool:
        return await super().is_responsible(event) and (
            event.data["op"] == "create"
            or event.data["op"] == "update"
            or event.data["op"] == "delete"
        )

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        if event.data["op"] == "create":
            for stream in event.data["streams"]:
                await self._handle_stream(stream["name"], stream["invite_only"])

        elif event.data["op"] == "delete":
            for stream in event.data["streams"]:
                self._remove_stream_from_table(stream["name"])

        elif event.data["op"] == "update":
            if event.data["property"] == "invite_only":
                await self._handle_stream(event.data["name"], event.data["value"])
            elif event.data[
                "property"
            ] == "name" and not self.client.private_stream_exists(event.data["name"]):
                # Remove the previous stream name from the database.
                self._remove_stream_from_table(event.data["name"])
                # Add the new stream name.
                await self._handle_stream(event.data["value"], False)

        return Response.none()

    async def _handle_stream(self, stream_name: str, private: bool) -> None:
        """Do the actual subscribing.

        Additionally, keep the list of public streams in the database
        up-to-date.
        """
        if private:
            self._remove_stream_from_table(stream_name)
            return
        
        try:
            with DB.session() as session:
                session.merge(PlublicStreams(StreamName=stream_name, Subscribed=0))
                session.commit()

                if await self.client.subscribe_users([self.client.id], stream_name):
                    session.query(PlublicStreams).filter_by(StreamName=stream_name).update(
                        {PlublicStreams.Subscribed: 1}
                    )
                    session.commit()
                    self.logger.info("subscribed to %s", stream_name)
                else:
                    self.logger.warning("could not subscribe to %s", stream_name)
        except Exception as e:
            self.logger.exception(e)

    def _remove_stream_from_table(self, stream_name: str) -> None:
        """Remove the given stream name from the PublicStreams table."""
        try:
            with DB.session() as session:
                session.query(PlublicStreams).filter_by(StreamName=stream_name).delete()
                session.commit()
        except Exception as e:
            self.logger.exception(e)
