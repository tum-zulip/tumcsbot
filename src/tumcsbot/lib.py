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
import re
import regex
import shlex
from enum import Enum
from importlib import import_module
from inspect import cleandoc, getmembers, isclass, ismodule
from itertools import repeat

from typing import Any, Callable, Final, Iterable, Type, TypeVar, cast


T = TypeVar("T")


LOGGING_FORMAT: Final[
    str
] = "[%(levelname)-8s] (%(asctime)s) | %(threadName)-15s| %(module)-15s| %(funcName)-15s: %(message)s"


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


class Regex:
    """Some widely used regex methods."""

    _USER_ARGUMENT_PATTERN = regex.compile(r"@_?\*\*.*?\*\*\s*")
    _GROUP_ARGUMENT_PATTERN = regex.compile(r"@_?\*.*?\*\s*")
    _STREAM_ARGUMENT_PATTERN = regex.compile(r"#\*\*.*?\*\*\s*")
    _REACTION_ARGUMENT_PATTERN = regex.compile(r":.+:")

    _USER_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"data-user-id=\"(?P<id>\d+)\""
    )
    _STREAM_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"data-stream-id=\"(?P<id>\d+)\""
    )
    _USER_GROUP_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"data-user-group-id=\"(?P<id>\d+)\""
    )

    _ARGUMENT_PATTERN = regex.compile(
        r"(?P<args>@_?\*\*.*?\*\*\s*|@_\*.*?\*\s*|#\*\*.*?\*\*\s*|'(\\\\|\\.|.)*?'\s*|"
        + r'"(\\\\|\\.|.)*?"\s*'
        + r"|\S*\s*)*"
    )

    _ASTERISKS: Final[re.Pattern[str]] = re.compile(r"(?:\*\*)")
    _OPT_ASTERISKS: Final[re.Pattern[str]] = re.compile(r"(?:{}|)".format(_ASTERISKS))
    _EMOJI: Final[re.Pattern[str]] = re.compile(r"[^:]+")
    _EMOJI_AUTOCOMPLETED_CAPTURE: Final[re.Pattern[str]] = re.compile(
        r":({}):".format(_EMOJI.pattern)
    )
    _TOPIC: Final[re.Pattern[str]] = re.compile(r".+")
    # Note: Currently, there are no further restrictions on stream names posed
    # by Zulip. That is why we cannot enforce sensible restrictions here.
    _STREAM: Final[re.Pattern[str]] = re.compile(r".+")
    _STREAM_AUTOCOMPLETED_CAPTURE: Final[re.Pattern[str]] = re.compile(
        r"#{0}({1}){0}".format(_ASTERISKS.pattern, _STREAM.pattern)
    )
    _STREAM_AND_TOPIC_AUTOCOMPLETED_CAPTURE: Final[re.Pattern[str]] = re.compile(
        r"#{0}({1})>({2}){0}".format(_ASTERISKS.pattern, r"[^>]+", _TOPIC.pattern)
    )
    _USER: Final[re.Pattern[str]] = re.compile(r"[^\*\`\\\>\"\@]+")
    _USER_AUTOCOMPLETED_TEMPLATE: str = r"{0}({1}){0}".format(
        _ASTERISKS.pattern, _USER.pattern
    )
    _USER_AUTOCOMPLETED_ID_TEMPLATE: str = r"{0}({1})\|(\d+){0}".format(
        _ASTERISKS.pattern, _USER.pattern
    )
    _USER_LINKED_CAPTURE: Final[re.Pattern[str]] = re.compile(
        r"@_" + _USER_AUTOCOMPLETED_TEMPLATE
    )
    _USER_MENTIONED_CAPTURE: Final[re.Pattern[str]] = re.compile(
        r"@" + _USER_AUTOCOMPLETED_TEMPLATE
    )
    _USER_LINKED_ID_CAPTURE: Final[re.Pattern[str]] = re.compile(
        r"@_" + _USER_AUTOCOMPLETED_ID_TEMPLATE
    )
    _USER_MENTIONED_ID_CAPTURE: Final[re.Pattern[str]] = re.compile(
        r"@" + _USER_AUTOCOMPLETED_ID_TEMPLATE
    )

    @staticmethod
    def get_captured_string_from_match(
        match: re.Match[str] | None, capture_group_id: int
    ) -> str | None:
        """Return the string of a capture group from a match.

        Return None if the match is None or if there is no capture group
        with the given index or if the expression of the capture group
        in the original regular expression could not be matched.
        """
        if match is None:
            return None
        try:
            return match.group(capture_group_id)
        except:
            return None

    @classmethod
    def get_captured_strings_from_pattern_or(
        cls, patterns: list[tuple[re.Pattern[str], list[int]]], string: str
    ) -> list[str] | None:
        """Extract a substring from a string.

        Walk through the provided patterns, find the first that matchs
        the given string (fullmatch) and extract the capture groups with
        the given ids.
        Return None if there has been no matching pattern.
        """
        for pattern, group_ids in patterns:
            match: re.Match[str] | None = pattern.fullmatch(string)
            if match is None:
                continue
            result: list[str | None] = [
                cls.get_captured_string_from_match(match, group_id)
                for group_id in group_ids
            ]
            return None if None in result else cast(list[str], result)

        return None

    @classmethod
    def get_emoji_name(cls, string: str) -> str | None:
        """Extract the emoji name from a string.

        Match the whole string.
        Emoji names may be of the following forms:
           <name>, :<name>:

        Leading/trailing whitespace is discarded.
        Return None if no match could be found.
        """
        result: list[str] | None = cls.get_captured_strings_from_pattern_or(
            [(cls._EMOJI_AUTOCOMPLETED_CAPTURE, [1]), (cls._EMOJI, [0])], string.strip()
        )
        return None if not result else result[0]

    @classmethod
    def get_stream_name(cls, string: str) -> str | None:
        """Extract the stream name from a string.

        Match the whole string.
        There are two cases handled here:
           abc -> abc, #**abc** -> abc

        Leading/trailing whitespace is discarded.
        Return None if no match could be found.
        """
        result: list[str] | None = cls.get_captured_strings_from_pattern_or(
            [(cls._STREAM_AUTOCOMPLETED_CAPTURE, [1]), (cls._STREAM, [0])],
            string.strip(),
        )
        return None if not result else result[0]

    @classmethod
    def get_stream_and_topic_name(cls, string: str) -> tuple[str, str | None] | None:
        """Extract the stream and the topic name from a string.

        Match the whole string and try to be smart:
           direct topic links: #**stream name>topic name**
                            -> (stream name, topic name)
           stream links: #**stream_name** -> (stream_name, None)
           plain stream names: stream_name -> (stream_name, None)

        Leading/trailing whitespace is discarded.
        Return None if no match could be found.

        Note that there may not occur a `>`-character in the stram name.
        This is related to the current behavior of the Zulip server and
        would need to be changed there.
        """
        result: list[str] | None = cls.get_captured_strings_from_pattern_or(
            [
                (cls._STREAM_AND_TOPIC_AUTOCOMPLETED_CAPTURE, [1, 2]),
                (cls._STREAM_AUTOCOMPLETED_CAPTURE, [1]),
                (cls._STREAM, [0]),
            ],
            string.strip(),
        )
        return (
            None if not result else (result[0], result[1] if len(result) > 1 else None)
        )

    @classmethod
    def get_user_name(
        cls, string: str, get_user_id: bool = False
    ) -> str | tuple[str, int | None] | None:
        """Extract the user name from a string.

        Match the whole string.
        There are five cases handled here:
           abc -> abc, @**abc** -> abc, @_**abc** -> abc
        and
           @**abc|1234** -> abc, @_**abc|1234** -> abc
           or - if get_user_id is True -
           @**abc|1234** -> (abc, 1234), @_**abc|1234** -> (abc, 1234)

        Leading/trailing whitespace is discarded.
        Return None if no match could be found.
        """
        result: list[str] | None = cls.get_captured_strings_from_pattern_or(
            [
                (cls._USER_MENTIONED_ID_CAPTURE, [1, 2]),
                (cls._USER_LINKED_ID_CAPTURE, [1, 2]),
                (cls._USER_MENTIONED_CAPTURE, [1]),
                (cls._USER_LINKED_CAPTURE, [1]),
                (cls._USER, [0]),
            ],
            string.strip(),
        )
        if not result:
            return None
        if not get_user_id:
            return result[0]
        if len(result) == 1:
            # We wanted the user ID, but did not find it.
            return (result[0], None)
        return (result[0], int(result[1]))

    @staticmethod
    def match_user_argument(s: str) -> str:
        if Regex._USER_ARGUMENT_PATTERN.match(s):
            return s
        else:
            raise ValueError()

    @staticmethod
    def match_group_argument(s: str) -> str:
        if (
            Regex._USER_ARGUMENT_PATTERN.match(s)
            or Regex._STREAM_ARGUMENT_PATTERN.match(s)
            or Regex._REACTION_ARGUMENT_PATTERN.match(s)
        ):
            raise ValueError()
        return s
        # todo: enable as soo as zulip api allows usergroups
        # if Regex._GROUP_ARGUMENT_PATTERN.match(s):
        #     return s
        # else:
        #     raise ValueError()

    @staticmethod
    def match_stream_argument(s: str) -> str:
        if Regex._STREAM_ARGUMENT_PATTERN.match(s):
            return s
        else:
            raise ValueError()

    @staticmethod
    def match_reaction_argument(s: str) -> str:
        if Regex._REACTION_ARGUMENT_PATTERN.match(s):
            return s
        else:
            raise ValueError()


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
        You don't have sufficient privileges to execute the command `{}`.
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
    def privilege_excpetion(cls, message: dict[str, Any], desc:str) -> "Response":
        """The user has not sufficient rights.

        Tell the user that they have not sufficient privileges for a
        specified command.
        """
        return cls.build_message(
            message, cls.excp_general.format(message["sender_full_name"],desc)
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


def get_classes_from_path(module_path: str, class_type: Type[T]) -> Iterable[Type[T]]:
    plugin_classes: list[Type[T]] = []
    for _, module in getmembers(import_module(module_path), ismodule):
        for _, value in getmembers(module, isclass):
            if value.__module__ == module.__name__ and issubclass(value, class_type):
                plugin_classes.append(value)  # pyright: ignore

    return plugin_classes


def split(
    string: str,
    sep: str | None = None,
    exact_split: int = 0,
    discard_empty: bool = True,
    converter: list[Callable[[str], Any]] | None = None,
) -> list[Any] | None:
    """Similar to the default split, but respects quotes.

    Basically, it's a wrapper for shlex.

    Arguments:
    ----------
    string         The string to split.
    sep            The delimiter to split on may be any string, but is
                   not supposed to contain quotation characters.
    exact_split    If the resulting list after splitting has not
                   exact_split elements, return None.
                   Values <= 0 will be ignored.
                   Note that exact_split is verified **after**
                   discarding empty strings (if discard_empty is true).
    discard_empty  Discard empty strings as splitting result (before
                   applying any converter).
    converter      A list of functions to be applied to each token.
                   If there are more token than converter, the last
                   converter will be used for every remaining token.
                   A converter may return None to indicate an error.

    Whitespace around the resulting tokens will be removed.
    Return None if there has been an error.
    """

    def exec_converter(conv: Callable[[str], Any], arg: str) -> Any:
        try:
            result: Any = conv(arg)
        except:
            return None
        return result

    if string is None:
        return None

    parser: shlex.shlex = shlex.shlex(
        instream=string, posix=True, punctuation_chars=False
    )
    # Do not handle comments.
    parser.commenters = ""
    # Split only on the characters specified as "whitespace".
    parser.whitespace_split = True
    if sep:
        parser.whitespace = sep

    try:
        result: list[Any] = list(map(str.strip, parser))
    except:
        return None

    if discard_empty:
        result = list(filter(lambda s: s, result))

    if exact_split > 0 and len(result) != exact_split:
        return None

    if converter:
        # Apply converter if present.
        len_result: int = len(result)
        len_converter: int = len(converter)

        if len_converter < len_result:
            converter.extend(repeat(converter[-1], len_result - len_converter))

        result = [
            exec_converter(conv, token) for (conv, token) in zip(converter, result)
        ]

    return result


def stream_names_equal(stream_name1: str, stream_name2: str) -> bool:
    """Decide whether two stream names are equal.

    Currently, Zulip considers stream names to be case insensitive.
    """
    return stream_name1.casefold() == stream_name2.casefold()


def stream_name_match(stream_reg: str, stream_name: str) -> bool:
    """Decide whether a stream regex matches a stream_name (fullmatch).

    Currently, Zulip considers stream names to be case insensitive.
    """
    return re.fullmatch(stream_reg, stream_name, flags=re.I) is not None


def validate_and_return_regex(regex: str | None) -> str | None:
    """Validate a regex and return it.

    Return None in case the regex is invalid.
    """
    if regex is None:
        return None
    try:
        re.compile(regex)
        return regex
    except re.error:
        return None
