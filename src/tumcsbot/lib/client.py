#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""
Wrapper around Zulip's Client class.
"""

from __future__ import annotations

import asyncio
from anyio import Event
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import re
from collections.abc import Iterable as IterableClass
from typing import AsyncGenerator, Callable, cast, Any, IO, Iterable, Final, final
from urllib.parse import quote

from sqlalchemy import Boolean, Column, String

from zulip import Client as ZulipClient

from tumcsbot.lib.db import DB, TableBase
from tumcsbot.lib.response import Response, MessageType
from tumcsbot.lib.regex import Regex
from tumcsbot.lib.utils import stream_names_equal


@final
@dataclass
class PluginContext:
    """All information a plugin may need.

    Parameters:
    -------
    id             The bots user id in zulip.
    ping           The string to ping the bot.
    zuliprc        The bot's zuliprc in case the plugin need an own
                   client instance.
    push_loopback  Method to push an event to the central event queue of
                   the bot.
    logging_level  The logging level to be used.
    """

    bot_id: int
    bot_mention: str
    zuliprc: str
    push_loopback: Callable[[Event], None]
    logging_level: Any


class PlublicStreams(TableBase):  # type: ignore
    __tablename__ = "PublicStreams"

    StreamName = Column(String, primary_key=True)
    Subscribed = Column(Boolean, nullable=False)


TTL: int = 10


class AsyncClient:
    """Wrapper around zulip.Client.

    Most of the code is copied from the original zulip.Client class.

    Additional attributes:
      id         direct access to get_profile()['user_id']
      ping       string used to ping the bot "@**<bot name>**"
      ping_len   len(ping)

    Additional Methods:
    -------------------
    get_public_stream_names   Get the names of all public streams.
    get_raw_message           Adapt original code and add apply_markdown.
    get_streams_from_regex    Get the names of all public streams
                              matching a regex.
    get_stream_name           Get stream name for provided stream id.
    get_user_ids_from_attribute
        Get the user ids from a given user attribute.
    get_user_ids_from_display_names
        Get the user id from a user display name.
    get_user_ids_from_emails
        Get the user id from a user email address.
    private_stream_exists     Check if there is a private stream with
                              the given name.
    send_response             Send one single response.
    send_responses            Send a list of responses.
    subscribe_all_from_stream_to_stream
                              Try to subscribe all users from one public
                              stream to another.
    subscribe_users           Subscribe a list of user ids to a public
                              stream.
    """

    def __init__(self, plugin_context: PluginContext, *args, **kwargs) -> None:
        self.id: int = plugin_context.bot_id
        self.ping: str = plugin_context.bot_mention
        self.ping_len: int = len(self.ping)
        self.register_params: dict[str, Any] = {}
        self._client: ZulipClient = ZulipClient(
            *args, config_file=plugin_context.zuliprc, insecure=True, **kwargs
        )
        self._executor = ThreadPoolExecutor()

    @property
    def base_url(self) -> str:
        return self._client.base_url

    def as_sync(self):
        return self._client

    async def call_endpoint(
        self,
        url: str | None = None,
        method: str = "POST",
        request: dict[str, Any] | None = None,
        longpolling: bool = False,
        files: list[IO[Any]] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        This is the backend for almost all API-user facing methods.
        Automatically resend requests if they failed because of the
        API rate limit.
        """
        result: dict[str, Any] = {}
        loop = asyncio.get_running_loop()

        while True:
            try:
                result = await loop.run_in_executor(
                    self._executor,
                    lambda: self._client.call_endpoint(
                        url, method, request, longpolling, files, timeout
                    ),
                )
            except asyncio.CancelledError as e:
                self._executor.shutdown(cancel_futures=True)
                raise e

            if not (
                result["result"] == "error"
                and "code" in result
                and result["code"] == "RATE_LIMIT_HIT"
            ):
                break

            secs: float = result.get("retry-after", 1)
            logging.warning("hit API rate limit, waiting for %f seconds...", secs)
            await asyncio.sleep(secs)
        return result

    async def render_message(
        self, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Example usage:

        >>> await client.render_message(request=dict(content='foo **bar**'))
        {u'msg': u'', u'rendered': u'<p>foo <strong>bar</strong></p>', u'result': u'success'}
        """
        return await self.call_endpoint(
            url="messages/render",
            method="POST",
            request=request,
        )

    async def get_streams(self, **request: Any) -> dict[str, Any]:
        """
        See examples/get-public-streams for example usage.
        """
        return await self.call_endpoint(
            url="streams",
            method="GET",
            request=request,
        )

    async def get_events(self, **request: Any) -> dict[str, Any]:
        """
        See the register() method for example usage.
        """
        return await self.call_endpoint(
            url="events",
            method="GET",
            longpolling=True,
            request=request,
        )

    async def events(
        self,
        event_types: list[str] | None = None,
        narrow: list[list[str]] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, dict[str, Any]]:
        if narrow is None:
            narrow = []

        async def do_register() -> tuple[str, int]:
            while True:
                if event_types is None:
                    res = await self.register(None, None, **kwargs)
                else:
                    res = await self.register(event_types, narrow, **kwargs)
                if "error" in res["result"]:
                    if self.verbose:
                        logging.error(f"Server returned error:\n{res['msg']}")
                    await asyncio.sleep(1)
                else:
                    return res["queue_id"], res["last_event_id"]

        queue_id, last_event_id = None, None

        while True:
            if queue_id is None:
                queue_id, last_event_id = await do_register()

            try:
                res = await self.get_events(
                    queue_id=queue_id, last_event_id=last_event_id
                )  # Ensure this is adapted to be async if necessary
            except Exception as e:
                # Handle exceptions appropriately, including logging and sleeping
                if self.verbose:
                    logging.exception(e)
                await asyncio.sleep(1)
                continue

            if "error" in res["result"]:
                # Handle various error cases, including server errors and bad event queue ids
                logging.error(
                    "Server returned error: %s", res.get("msg", "Unknown error")
                )
                if res.get("code") == "BAD_EVENT_QUEUE_ID":
                    queue_id = (
                        None  # Force re-registration if the event queue ID is bad
                    )
                await asyncio.sleep(1)  # Prevent rapid re-request on error
                continue

            for event in res["events"]:
                last_event_id = max(last_event_id, int(event["id"]))

                if event["type"] == "heartbeat":
                    continue  # Skip heartbeat events

                yield event  # yield the next relevant event

    async def get_messages(self, message_filters: dict[str, Any]) -> dict[str, Any]:
        """Override zulip.Client.get_messages.

        Defaults to 'apply_markdown' = False.
        See examples/get-messages for example usage
        """
        if "apply_markdown" not in message_filters:
            message_filters["apply_markdown"] = False
        return await self.call_endpoint(
            url="messages", method="GET", request=message_filters
        )

    async def get_public_stream_names(self, use_db: bool = True) -> list[str]:
        """Get the names of all public streams.

        Use the database in conjunction with the plugin "autosubscriber"
        to avoid unnecessary network requests.
        In case of an error, return an empty list.
        """

        async def without_db() -> list[str]:
            result: dict[str, Any] = await self.get_streams(
                include_public=True, include_subscribed=False
            )
            if result["result"] != "success":
                return []
            return list(map(lambda d: cast(str, d["name"]), result["streams"]))

        if not use_db:
            return await without_db()

        try:
            with DB.session() as session:
                return list(
                    map(
                        lambda t: cast(str, t[0]),
                        session.query(PlublicStreams.StreamName).all(),
                    )
                )
        except Exception as e:
            logging.exception(e)
            return await without_db()

    async def get_raw_message(
        self, message_id: int, apply_markdown: bool = True
    ) -> dict[str, Any]:
        """Adapt original code and add apply_markdown."""
        return await self.call_endpoint(
            url=f"messages/{message_id}",
            method="GET",
            request={"apply_markdown": apply_markdown},
        )

    async def get_streams_from_regex(self, regex: str) -> list[str]:
        """Get the names of all public streams matching a regex.

        The regex has to match the full stream name.
        Note that Zulip handles stream names case insensitively at the
        moment.

        Return an empty list if the regex is not valid.
        """
        if not regex:
            return []

        try:
            pat: re.Pattern[str] = re.compile(regex, flags=re.I)
        except re.error:
            return []

        return [
            stream_name
            for stream_name in await self.get_public_stream_names()
            if pat.fullmatch(stream_name)
        ]

    async def get_stream_name(self, stream_id: int) -> str | None:
        """Get stream name for provided stream id.

        Return the stream name as string or None if the stream name
        could not be determined.
        """
        result: dict[str, Any] = await self.get_streams(include_all_active=True)
        if result["result"] != "success":
            return None

        for stream in result["streams"]:
            if stream["stream_id"] == stream_id:
                return cast(str, stream["name"])

        return None

    async def get_user_by_id(self, user_id: int, **request: Any) -> dict[str, Any]:
        """
        Example usage:

        >>> await client.get_user_by_id(8, include_custom_profile_fields=True)
        {'result': 'success', 'msg': '', 'user': [{...}, {...}]}
        """
        return await self.call_endpoint(
            url=f"users/{user_id}",
            method="GET",
            request=request,
        )

    async def get_user_ids_from_active_status(
        self, active: bool = True
    ) -> list[int] | None:
        """Get all user ids which are (de)activated."""
        return await self.get_user_ids_from_attribute("is_active", [active])

    async def get_user_ids_from_attribute(
        self, attribute: str, values: Iterable[Any], case_sensitive: bool = True
    ) -> list[int] | None:
        """Get the user ids from a given user attribute.

        Get and return a list of user ids of all users whose profiles
        contain the attribute "attribute" with a value present in
        "values".
        If case_sensitive is set to False, the values will be
        interpreted as strings and compared case insensitively.
        Return None on error.
        """
        result: dict[str, Any] = await self.get_users()
        if result["result"] != "success":
            return None

        if not case_sensitive:
            values = map(lambda x: str(x).lower(), values)

        value_set: set[Any] = set(values)

        return [
            user["user_id"]
            for user in result["members"]
            if attribute in user
            and (
                user[attribute] in value_set
                if case_sensitive
                else str(user[attribute]).lower() in value_set
            )
        ]

    async def get_user_ids_from_display_names(
        self, display_names: Iterable[str]
    ) -> list[int] | None:
        """Get the user id from a user display name.

        Since there may be multiple users with the same display name,
        the returned list of user ids may be longer than the given list
        of user display names.
        Return None on error.
        """
        return await self.get_user_ids_from_attribute("full_name", display_names)

    async def get_user_ids_from_emails(self, emails: Iterable[str]) -> list[int] | None:
        """Get the user id from a user email address.

        Return None on error.
        """
        return await self.get_user_ids_from_attribute(
            "delivery_email", emails, case_sensitive=False
        )

    async def get_users(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """Override method from parent class."""
        # Try to minimize the network traffic.
        if request is not None:
            request.update(client_gravatar=True, include_custom_profile_fields=False)
        return await self.call_endpoint(
            url="users",
            method="GET",
            request=request,
        )

    async def delete_message(self, message_id: int) -> dict[str, Any]:
        return await self.call_endpoint(url=f"messages/{message_id}", method="DELETE")

    async def get_message_by_id(self, message_id: int) -> dict[str, Any]:
        result: dict[str, Any] = await self.call_endpoint(
            url=f"messages/{message_id}", method="GET"
        )
        return result["message"]

    async def get_profile(
        self, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Example usage:

        >>> await client.get_profile()
        {u'user_id': 5, u'full_name': u'Iago', u'short_name': u'iago', ...}
        """
        return await self.call_endpoint(
            url="users/me",
            method="GET",
            request=request,
        )

    def is_only_pm_recipient(self, message: dict[str, Any]) -> bool:
        """Check whether the bot is the only recipient of the given pm.

        Check whether the message is a private message and the bot is
        the only recipient.
        """
        if not message["type"] == "private" or message["sender_id"] == self.id:
            return False

        # Note that the list of users who received the pm includes the sender.

        recipients: list[dict[str, Any]] = message["display_recipient"]
        if len(recipients) != 2:
            return False

        return self.id in [recipients[0]["id"], recipients[1]["id"]]

    async def private_stream_exists(self, stream_name: str) -> bool:
        """Check if there is a private stream with the given name.

        Return true if there is a private stream with the given name.
        Return false if there is no stream with this name or if the
        stream is not private.
        """
        result: dict[str, Any] = await self.get_streams(include_all_active=True)
        if result["result"] != "success":
            return False  # TODO?

        for stream in result["streams"]:
            if stream_names_equal(stream["name"], stream_name):
                return bool(stream["invite_only"])

        return False

    async def register(
        self,
        event_types: Iterable[str] | None = None,
        narrow: list[list[str]] | None = None,
        **kwargs: object,
    ) -> dict[str, Any]:
        """
        Example usage:

        >>> await client.register(['message'])
        {u'msg': u'', u'max_message_id': 112, u'last_event_id': -1, u'result': u'success', u'queue_id': u'1482093786:2'}
        >>> client.get_events(queue_id='1482093786:2', last_event_id=0)
        {...}
        """

        if narrow is None:
            narrow = []

        logging.debug("event_types: %s, narrow: %s", str(event_types), str(narrow))
        request = request = dict(
            event_types=event_types, narrow=narrow, **self.register_params, **kwargs
        )

        return await self.call_endpoint(
            url="register",
            request=request,
        )

    async def deregister(
        self, queue_id: str, timeout: float | None = None
    ) -> dict[str, Any]:
        """
        Example usage:

        >>> await client.register(['message'])
        {u'msg': u'', u'max_message_id': 113, u'last_event_id': -1, u'result': u'success', u'queue_id': u'1482093786:3'}
        >>> await client.deregister('1482093786:3')
        {u'msg': u'', u'result': u'success'}
        """
        request = dict(queue_id=queue_id)

        return await self.call_endpoint(
            url="events",
            method="DELETE",
            request=request,
            timeout=timeout,
        )

    async def send_message(self, message_data: dict[str, Any]) -> dict[str, Any]:
        return await self.call_endpoint(
            url="messages",
            request=message_data,
        )

    async def add_reaction(self, reaction_data: dict[str, Any]) -> dict[str, Any]:
        """
        Example usage:

        >>> await client.add_reaction({
            'message_id': 100,
            'emoji_name': 'joy',
            'emoji_code': '1f602',
            'reaction_type': 'unicode_emoji'
        })
        {'result': 'success', 'msg': ''}
        """
        return await self.call_endpoint(
            url="messages/{}/reactions".format(reaction_data["message_id"]),
            method="POST",
            request=reaction_data,
        )

    async def remove_reaction(self, reaction_data: dict[str, Any]) -> dict[str, Any]:
        """
        Example usage:

        >>> await client.remove_reaction({
            'message_id': 100,
            'emoji_name': 'joy',
            'emoji_code': '1f602',
            'reaction_type': 'unicode_emoji'
        })
        {'msg': '', 'result': 'success'}
        """
        return await self.call_endpoint(
            url="messages/{}/reactions".format(reaction_data["message_id"]),
            method="DELETE",
            request=reaction_data,
        )

    async def delete_stream(self, stream_id: int) -> dict[str, Any]:
        return await self.call_endpoint(
            url=f"streams/{stream_id}",
            method="DELETE",
        )

    async def send_response(self, response: Response) -> dict[str, Any]:
        """Send one single response."""
        logging.debug("send_response: %s", str(response))

        if response.message_type == MessageType.MESSAGE:
            return await self.send_message(response.response)
        if response.message_type == MessageType.EMOJI:
            return await self.add_reaction(response.response)
        return {}

    async def send_responses(
        self,
        responses: Response | Iterable[Response | Iterable[Response]],
    ) -> None:
        """Send the given responses."""
        if responses is None:
            logging.debug("responses is None, this should never happen")
            return

        if not isinstance(responses, IterableClass):
            await self.send_response(responses)
            return

        for response in responses:
            await self.send_responses(response)

    async def get_stream_id(self, stream: str) -> dict[str, Any]:
        """
        Example usage: await client.get_stream_id('devel')
        """
        stream_encoded = quote(stream, safe="")
        url = f"get_stream_id?stream={stream_encoded}"
        return await self.call_endpoint(
            url=url,
            method="GET",
            request=None,
        )

    async def get_subscribers(self, **request: Any) -> dict[str, Any]:
        """
        Example usage: await client.get_subscribers(stream='devel')
        """
        response = await self.get_stream_id(request["stream"])
        if response["result"] == "error":
            return response

        stream_id = response["stream_id"]
        url = "streams/%d/members" % (stream_id,)
        return await self.call_endpoint(
            url=url,
            method="GET",
            request=request,
        )

    async def subscribe_all_from_stream_to_stream(
        self, from_stream: str, to_stream: str, description: str | None = None
    ) -> bool:
        """Try to subscribe all users from one public stream to another.

        Arguments:
        ----------
        from_stream   An existant public stream.
        to_stream     The stream to subscribe to.
                      Must be public, if already existant. If it does
                      not already exists, it will be created.
        description   An optional description to be used to
                      create the stream first.

        Return true on success or false otherwise.
        """
        if await self.private_stream_exists(
            from_stream
        ) or await self.private_stream_exists(to_stream):
            return False

        subs: dict[str, Any] = await self.get_subscribers(stream=from_stream)
        if subs["result"] != "success":
            return False

        return await self.subscribe_users(subs["subscribers"], to_stream, description)

    async def subscribe_users(
        self,
        user_ids: list[int],
        stream_name: str,
        description: str | None = None,
        allow_private_streams: bool = False,
        filter_active: bool = True,
    ) -> bool:
        """Subscribe a list of user ids to a public stream.

        Arguments:
        ----------
        user_ids      The list of user ids to subscribe.
        stream_name   The name of the stream to subscribe to.
        description   An optional description to be used to
                      create the stream first.
        allow_private_streams
                      Allow subscription to private streams.
        filter_active
                      Remove non-active users from the request.
                      Users are not active when they are deactivated
                      or deleted.

        Return true on success or false otherwise.
        """
        return (
            await self.subscribe_users_multiple_streams(
                user_ids=user_ids,
                streams=[(stream_name, description)],
                allow_private_streams=allow_private_streams,
                filter_active=filter_active,
            )
        )[0]

    async def add_subscriptions(
        self, streams: Iterable[dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        request = dict(subscriptions=streams, **kwargs)

        return await self.call_endpoint(
            url="users/me/subscriptions",
            request=request,
        )

    async def remove_subscriptions(
        self, id: int, streams: Iterable[dict[str, Any]]
    ) -> dict[str, Any]:
        request = dict(subscriptions=streams, principals=[id])

        return await self.call_endpoint(
            url="users/me/subscriptions", method="DELETE", request=request
        )

    async def subscribe_users_multiple_streams(
        self,
        user_ids: list[int],
        streams: list[tuple[str, str | None]],
        allow_private_streams: bool = False,
        filter_active: bool = True,
    ) -> tuple[bool, str | None]:
        """Subscribe a list of user ids to a list of public streams.

        Arguments:
        ----------
        user_ids      The list of user ids to subscribe.
        streams       A list of (stream name, stream description) tuples
                      denoting the streams to subscribe the given users
                      to. The strams will be created first in case they
                      don't exist. The stream description can be None.
        allow_private_streams
                      Allow subscription to private streams.
                      If false, every private stream in `streams` will
                      not be used for subscribing.
        filter_active
                      Remove non-active users from the request.
                      Users are not active when they are deactivated
                      or deleted.

        Return (True, None) on success or (False, error message)
        otherwise.
        """
        chunk_size: int = 100

        if not allow_private_streams:
            streams = [
                (name, description)
                for name, description in streams
                if not await self.private_stream_exists(name)
            ]
            if not streams:
                return (True, None)

        if filter_active:
            active_user_ids: list[int] | None = (
                await self.get_user_ids_from_active_status()
            )
            if active_user_ids is None:
                logging.error("cannot retrieve active user ids")
                return (False, "cannot retrieve active user ids")
            user_ids = list(set(user_ids) & set(active_user_ids))

        subscriptions: list[dict[str, str]] = [
            (
                {"name": name}
                if description is None
                else {"name": name, "description": description}
            )
            for name, description in streams
        ]

        success: bool = True
        errs: list[str] = []

        for i in range(0, len(user_ids), chunk_size):
            # (a too large index will be automatically reduced to len())
            user_id_chunk: list[int] = user_ids[i : i + chunk_size]

            while True:
                result: dict[str, Any] = await self.add_subscriptions(
                    streams=subscriptions, principals=user_id_chunk
                )
                if result["result"] == "success":
                    break
                if result["code"] == "UNAUTHORIZED_PRINCIPAL" and "principal" in result:
                    user_id_chunk.remove(result["principal"])
                    continue
                success = False
                err: str = str(result)
                logging.warning(err)
                errs.append(err)
                break

        return (
            success,
            None if success else ("the following errors occurred: " + ",".join(errs)),
        )

    async def user_is_privileged(
        self, user_id: int, allow_moderator: bool = False
    ) -> bool:
        """Check whether a user is allowed to perform privileged commands.

        Arguments:
        ----------
            user_id          The user_id to examine.
            allow_moderator  Whether the moderator role should be
                             considered as privileged, too.
                             Defaults to False.
        """
        result: dict[str, Any] = await self.get_user_by_id(user_id)
        if result["result"] != "success":
            return False
        user: dict[str, Any] = result["user"]

        return (
            "role" in user
            and isinstance(user["role"], int)
            and user["role"] in [100, 200]
            or (allow_moderator and user["role"] == 300)
        )

    async def get_user_id_by_name(self, username: str) -> int | None:
        request = {
            "content": username,
        }

        result = await self.render_message(request)
        if result["result"] != "success":
            return None

        match = re.search(Regex._USER_ID_PATTERN, result["rendered"])
        if not match:
            return None
        return int(match.groupdict()["id"])

    async def get_stream_id_by_name(self, stream_name: str) -> int | None:
        request = {
            "content": stream_name,
        }

        result = await self.render_message(request)
        if result["result"] != "success":
            return None

        match = re.search(Regex._STREAM_ID_PATTERN, result["rendered"])
        if not match:
            return None
        return int(match.groupdict()["id"])

    async def get_group_id_by_name(self, group_name: str) -> int | None:
        request = {
            "content": group_name,
        }

        result = await self.render_message(request)
        if result["result"] != "success":
            return None

        match = re.search(Regex._USER_GROUP_ID_PATTERN, result["rendered"])
        if not match:
            return None
        return int(match.groupdict()["id"])

    async def get_stream_by_id(self, stream_id: int) -> dict[str, Any] | None:
        stream_result = await self.call_endpoint(
            url=f"/streams/{stream_id}", method="GET"
        )

        if stream_result["result"] != "success":
            return None

        stream_data: dict[str, Any] = stream_result["stream"]
        return stream_data

    async def mark_stream_as_read(self, stream_id: int) -> dict[str, Any]:
        """
        Example usage:

        >>> await client.mark_stream_as_read(42)
        {'result': 'success', 'msg': ''}
        """
        return await self.call_endpoint(
            url="mark_stream_as_read",
            method="POST",
            request={"stream_id": stream_id},
        )

    async def update_stream(self, stream_data: dict[str, Any]) -> dict[str, Any]:
        """
        See examples/edit-stream for example usage.
        """

        return await self.call_endpoint(
            url="streams/{}".format(stream_data["stream_id"]),
            method="PATCH",
            request=stream_data,
        )

    async def start_typing_direct(self, user_ids: int | list[int]) -> dict[str, Any]:
        if isinstance(user_ids, int):
            user_ids = [user_ids]

        request = {
            "op": "start",
            "to": user_ids,
        }
        return await self.call_endpoint(
            url="typing",
            request=request,
        )

    async def stop_typing_direct(self, user_ids: int | list[int]) -> dict[str, Any]:
        if isinstance(user_ids, int):
            user_ids = [user_ids]

        request = {
            "op": "stop",
            "to": user_ids,
        }
        return await self.call_endpoint(
            url="typing",
            request=request,
        )

    @asynccontextmanager
    async def typing_direct(
        self, user_ids: int | list[int]
    ) -> AsyncGenerator[None, None]:
        await self.start_typing_direct(user_ids)
        try:
            yield
        finally:
            await self.stop_typing_direct(user_ids)

    def trigger_dummy_event(self) -> None:
        request = {
            "op": "stop",
            "to": [self.id],
        }
        self._client.call_endpoint(
            url="typing",
            request=request,
        )
