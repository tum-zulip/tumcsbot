#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Manage reactions on certain words or phrases with emojis.

Use the Zulip facility, see https://zulip.com/help/add-an-alert-word.
Provide also an interactive command so administrators are able to
change the alert words and specify the emojis to use for the reactions.
"""

import urllib
from typing import Any, Iterable, Callable

from tumcsbot.lib.response import Response
from tumcsbot.lib.types import ZulipChannel, ZulipUser
from tumcsbot.plugin import Event, Plugin
from tumcsbot.lib.db import DB


from tumcsbot.plugins.moderate import (
    ReactionAction,
    ReactionConfig,
    ModerationConfig,
    ChannelAuthorization,
    GroupAuthorization,
)

from tumcsbot.plugins.usergroup import UserGroup, UserGroupMember
from tumcsbot.lib.types import AsyncClientMixin


class ModerationReactionHandler(Plugin):
    # pylint: disable=line-too-long
    _replace_dict: dict[
        str, tuple[Callable[[dict[str, Any], dict[str, Any]], str], str]
    ] = {
        "user": (
            lambda _, message: f"@**{message['sender_full_name']}|{message['sender_id']}**",
            "the sender of the message being reacted to",
        ),
        "mod": (
            lambda event_data, _: f"@**{event_data['user']['full_name']}|{event_data['user_id']}**",
            "the sender of the reaction",
        ),
        "channel": (
            lambda _, message: f"#**{message['display_recipient']}**",
            "the channel in which the reaction occurred",
        ),
        "topic": (
            lambda _, message: f"#**{message['display_recipient']}>{message['subject']}**",
            "the topic in which the reaction occurred",
        ),
        "escaped_topic": (
            lambda _, message: urllib.parse.quote(message["subject"]),
            "to topic as an html escaped string",
        ),
        "message": (
            lambda event_data, message: f"/#narrow/stream/{message['display_recipient']}/topic/{message['subject']}/near/{event_data['message_id']}",
            "link to the message being reacted to. Usage: `[Display Text]($message)`",
        ),
        "content": (
            lambda _, message: message["content"],
            "the content of the message being reacted to",
        ),
    }
    # pylint: enable=line-too-long

    async def is_responsible(self, event: Event) -> bool:
        return await super().is_responsible(event) or (
            event.data["type"] == "reaction"
            and event.data["op"] == "add"
            and event.data["user_id"] != self.client.id
        )

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        AsyncClientMixin.set_client(self.client)

        reaction_sender = ZulipUser(event.data["user_id"])
        await reaction_sender

        mid: int = event.data["message_id"]

        response = await self.client.call_endpoint(
            url=f"/messages/{mid}?apply_markdown=false", method="GET"
        )

        if response["result"] != "success":
            return Response.none()

        message = response["message"]

        if message["type"] != "stream":
            return Response.none()

        channel = ZulipChannel(message["stream_id"])
        await channel

        emote = event.data["emoji_name"]

        with DB.session() as session:
            actions = (
                session.query(ReactionAction)
                .join(ReactionConfig)
                .join(ModerationConfig)
                .join(GroupAuthorization)
                .join(UserGroup)
                .join(UserGroupMember)
                .join(ChannelAuthorization)
                .filter(ChannelAuthorization.Channel == channel)  # type: ignore
                .filter(UserGroupMember.User == reaction_sender)  # type: ignore
                .filter(ReactionConfig.emote == emote)
                .all()
            )

            responses = []
            for action in actions:
                ty = action.Action
                data = action.Data
                if ty == "delete":
                    await self.client.delete_message(mid)

                elif ty == "respond":
                    responses.append(
                        Response.build_message(
                            message,
                            content=ModerationReactionHandler._replace_placeholder(
                                str(data), event.data, message
                            ),
                        )
                    )

                elif ty == "dm":
                    responses.append(
                        Response.build_message(
                            message=None,
                            to=[message["sender_id"]],
                            msg_type="private",
                            content=ModerationReactionHandler._replace_placeholder(
                                str(data), event.data, message
                            ),
                        )
                    )

            if len(responses) == 0:
                return Response.none()
            return responses

    @staticmethod
    def _replace_placeholder(
        content: str, event_data: dict[str, Any], message: dict[str, Any]
    ) -> str:
        return content.format(
            **{
                k: replacement(event_data, message)
                for k, (
                    replacement,
                    _,
                ) in ModerationReactionHandler._replace_dict.items()
            }
        )
