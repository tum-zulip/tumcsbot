#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Keep the bot subscribed to all public channels.

Reason:
As the 'all_public_channels' parameter of the event API [1] does not
seem to work properly, we need a work-around in order to be able to
receive events for all public channels.

[1] https://zulip.com/api/register-queue#parameter-all_public_streams
"""

import asyncio
from typing import Iterable

from tumcsbot.lib.response import Response
from tumcsbot.plugin import Plugin
from tumcsbot.lib.db import DB

from tumcsbot.lib.client import PlublicChannels, Event


class AutoSubscriber(Plugin):
    """Keep the bot subscribed to all public channels."""

    zulip_events = ["stream"]

    def _init_plugin(self) -> None:
        self._db: DB = DB()
        # Ensure that we are subscribed to all existing channels.
        with DB.session() as session:
            channels = session.query(PlublicChannels).filter_by(Subscribed=0).all()
            channel_ids = [int(channel.ChannelId) for channel in channels]

            if channels:
                self.client.as_sync().add_subscriptions(
                    [{"stream_id": channel_id} for channel_id in channel_ids]
                )

    async def is_responsible(self, event: Event) -> bool:
        return await super().is_responsible(event) and (
            event.data["op"] == "create"
            or event.data["op"] == "update"
            or event.data["op"] == "delete"
        )

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        if event.data["op"] == "create":
            for channel in event.data["streams"]:
                await self._handle_channel(channel["stream_id"], channel["name"], channel["invite_only"])

        elif event.data["op"] == "delete":
            for channel in event.data["streams"]:
                self._remove_channel_from_table(channel["stream_id"])

        elif event.data["op"] == "update":
            if event.data["property"] == "invite_only":
                await self._handle_channel(event.data["stream_id"], event.data["name"], event.data["value"])
            elif event.data[
                "property"
            ] == "name" and not self.client.private_channel_exists(event.data["name"]):
                # Remove the previous channel name from the database.
                self._remove_channel_from_table(event.data["name"])
                # Add the new channel name.
                await self._handle_channel(event.data["stream_id"], event.data["value"], False)

        return Response.none()

    async def _handle_channel(
        self, channel_id: int, channel_name: str, private: bool
    ) -> None:
        """Do the actual subscribing.

        Additionally, keep the list of public channels in the database
        up-to-date.
        """
        if private:
            self._remove_channel_from_table(channel_id)
            return

        try:
            with DB.session() as session:
                session.merge(
                    PlublicChannels(
                        ChannelId=channel_id, ChannelName=channel_name, Subscribed=0
                    )
                )
                session.commit()

                if await self.client.subscribe_users([self.client.id], channel_name):
                    session.query(PlublicChannels).filter_by(
                        ChannelId=channel_id
                    ).update({PlublicChannels.Subscribed: 1})
                    session.commit()
                    self.logger.info("subscribed to %s", channel_name)
                else:
                    self.logger.warning("could not subscribe to %s", channel_name)
        except Exception as e:
            self.logger.exception(e)

    def _remove_channel_from_table(self, channel_id: int) -> None:
        """Remove the given channel name from the PublicChannels table."""
        try:
            with DB.session() as session:
                session.query(PlublicChannels).filter_by(
                    ChannelId=channel_id
                ).delete()
                session.commit()
        except Exception as e:
            self.logger.exception(e)
