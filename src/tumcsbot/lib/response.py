#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Collection of useful classes and functions.

Classes:
--------
MessageType     Enum describing the type of a message.
Regex           Some widely used regex methods.
CommandParser   A simple positional argument parser.
Conf            Manage the bot's configuration variables.
DB              Simple sqlite wrapper.
Response        Provide Response building methods.

Functions:
----------
split               Similar to the default split, but respects quotes.
stream_names_equal  Decide whether two stream names are equal.
"""

import json
from enum import Enum
from inspect import cleandoc

from typing import Any

class StrEnum(str, Enum):
    """Construct a string enum.

    See https://docs.python.org/3/library/enum.html#others.
    This own enum class is deprecated since Python 3.10 but is going
    to stay for some time in order to ensure compatibility.
    """

class MessageType(StrEnum):
    """Represent the type of a message.

    MESSAGE  Normal message as written by a human user.
    EMOJI    Emoji reaction on a message.
    NONE     No message.
    """

    MESSAGE = "message"
    EMOJI = "emoji"
    NONE = "none"

class Response:
    """Some useful methods for building a response message."""

    privilege_err_msg: str = cleandoc(
        """
        Hi {}!
        You don't have sufficient privileges to execute this command.
        """
    )

    privilege_err_msg_command: str = cleandoc(
        """
        Hi {}!
        You don't have sufficient privileges to execute the command `{}`. {}
        """
    )
    excp_general: str = cleandoc(
        """
        Hi {}!
        {}
        """
    )

    command_not_found_msg: str = cleandoc(
        """
        Hi {}!
        Unfortunately, I currently cannot understand what you wrote to me.
        Try "help" to get a glimpse of what I am capable of. :-)
        """
    )
    exception_msg: str = cleandoc(
        """
        Hi {}!
        An exception occurred while executing your request.
        Did you try to hack me? ;-)
        """
    )
    error_msg: str = cleandoc(
        """
        Sorry, {}, an error occurred while executing your request.
        """
    )
    request_msg: str = cleandoc(
        """
        Hi {}!
        Your input would lead to the execution of the following command.
        Do you want to execute this? If yes, please react with :check: to this message.
        original_message_id: {}
        command: {}
        """
    )
    greet_msg: str = "Hi {}! :-)"
    ok_emoji: str = "ok"
    no_emoji: str = "cross_mark"

    def __init__(self, message_type: MessageType, response: dict[str, Any]) -> None:
        self.message_type: MessageType = message_type
        self.response: dict[str, Any] = response

    def __repr__(self) -> str:
        return self.__str__()

    def __str__(self) -> str:
        return json.dumps(
            {"message_type": str(self.message_type), "response": str(self.response)}
        )

    def is_none(self) -> bool:
        """Check whether this response has the MessageType 'None'."""
        return self.message_type == MessageType.NONE

    @classmethod
    def build_message(
        cls,
        message: dict[str, Any] | None,
        content: str,
        msg_type: str | None = None,
        to: str | int | list[int] | list[str] | None = None,
        subject: str | None = None,
    ) -> "Response":
        """Build a message.

        Arguments:
        ----------
        message    The message to respond to.
                       May be explicitely set to None. In this case,
                       'msg_type', 'to' (and 'subject' if 'msg_type'
                       is 'stream') have to be specified.
        content   The content of the response.
        msg_type   Determine if the response should be a stream or a
                   private message. ('stream', 'private')
                   [optional]
        to         If it is a private message:
                       Either a list containing integer user IDs
                       or a list containing string email addresses.
                   If it is a stream message:
                       Either the name or the integer ID of a stream.
                   [optional]
        subject    The topic the message should be added to (only for
                   stream messages).
                   [optional]

        The optional arguments are inferred from 'message' if provided.

        Return a Response object.
        """
        if message is None and (
            msg_type is None or to is None or (msg_type == "stream" and subject is None)
        ):
            return cls.none()

        if message is not None:
            if msg_type is None:
                msg_type = message["type"]
            private: bool = msg_type == "private"

            if to is None:
                to = message["sender_email"] if private else message["stream_id"]

            if subject is None:
                subject = message["subject"] if not private else ""

        # 'subject' field is ignored for private messages
        # see https://zulip.com/api/send-message#parameter-topic
        return cls(
            MessageType.MESSAGE,
            {"type": msg_type, "to": to, "subject": subject, "content": content},
        )

    @classmethod
    def build_reaction(cls, message: dict[str, Any], emoji: str) -> "Response":
        """Build a reaction response.

        Arguments:
        ----------
        message   The message to react on.
        emoji     The emoji to react with.
        """
        return cls(
            MessageType.EMOJI, {"message_id": message["id"], "emoji_name": emoji}
        )

    @classmethod
    def build_reaction_from_id(cls, message_id: int, emoji: str) -> "Response":
        """Build a reaction response.

        Arguments:
        ----------
        message_id   The id of the message to react on.
        emoji        The emoji to react with.
        """
        return cls(MessageType.EMOJI, {"message_id": message_id, "emoji_name": emoji})

    @classmethod
    def build_request_msg(cls, message: dict[str, Any], command: str) -> "Response":
        """Build a message requesting whether the user wants to execute a given command.

        Arguments:
        ----------
        message     The message sent by the user.
        command     The command that would have been executed, given
                    the message.
        """
        return cls.build_message(
            message,
            cls.request_msg.format(message["sender_full_name"], message["id"], command),
        )

    @classmethod
    def privilege_err(cls, message: dict[str, Any]) -> "Response":
        """The user has not sufficient rights.

        Tell the user that they have not sufficient privileges for a
        certain command.
        """
        return cls.build_message(
            message, cls.privilege_err_msg.format(message["sender_full_name"])
        )
    
    @classmethod
    def privilege_excpetion(cls, message: dict[str, Any], comm: str, reason: str | None = None) -> "Response":
        """The user has not sufficient rights.

        Tell the user that they have not sufficient privileges for a
        specified command.
        """
        return cls.build_message(
            message, cls.privilege_err_msg_command.format(message["sender_full_name"], comm, reason or "")
        )
    
    @classmethod
    def command_not_found(cls, message: dict[str, Any]) -> "Response":
        """Tell the user that his command could not be found."""
        return cls.build_reaction(message, "question")

    @classmethod
    def error(cls, message: dict[str, Any]) -> "Response":
        """Tell the user that an error occurred."""
        return cls.build_message(
            message, cls.error_msg.format(message["sender_full_name"])
        )

    @classmethod
    def exception(cls, message: dict[str, Any]) -> "Response":
        """Tell the user that an exception occurred."""
        return cls.build_message(
            message, cls.exception_msg.format(message["sender_full_name"])
        )

    @classmethod
    def greet(cls, message: dict[str, Any]) -> "Response":
        """Greet the user."""
        return cls.build_message(
            message, cls.greet_msg.format(message["sender_full_name"])
        )

    @classmethod
    def ok(cls, message: dict[str, Any]) -> "Response":
        """Return an "ok"-reaction."""
        return cls.build_reaction(message, cls.ok_emoji)

    @classmethod
    def no(cls, message: dict[str, Any]) -> "Response":
        """Return a "no"-reaction."""
        return cls.build_reaction(message, cls.no_emoji)

    @classmethod
    def none(cls) -> "Response":
        """No response."""
        return cls(MessageType.NONE, {})

