from typing import Final, cast
import re
import regex

class Regex:
    """Some widely used regex methods."""

    _USER_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"data-user-id=\"(?P<id>\d+)\""
    )
    _STREAM_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"data-stream-id=\"(?P<id>\d+)\""
    )
    _USER_GROUP_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"data-user-group-id=\"(?P<id>\d+)\""
    )  

    # todo: (jr) docuemnt why two different regex libraries are used
    _ARGUMENT_PATTERN = regex.compile(
        r"(?P<args>@_?\*\*.*?\*\*\s*|@_\*.*?\*\s*|#\*\*.*?\*\*\s*|'(\\\\|\\.|.)*?'\s*"
        + r"|```([\s\S]*?)```"
        + r'|"(\\\\|\\.|.)*?"\s*'
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
           :<name>:

        Leading/trailing whitespace is discarded.
        Return None if no match could be found.
        """
        result: re.Match[str] | None = cls._EMOJI_AUTOCOMPLETED_CAPTURE.match(string.strip())
        return cls.get_captured_string_from_match(result, 1)

    @classmethod
    def get_stream_name(cls, string: str) -> str | None:
        """Extract the stream name from a string.

        Match the whole string.
        There are two cases handled here:
           #**abc** -> abc

        Leading/trailing whitespace is discarded.
        Return None if no match could be found.
        """
        result: re.Match[str] | None = cls._STREAM_AUTOCOMPLETED_CAPTURE.match(string.strip())
        return cls.get_captured_string_from_match(result, 1)

    @classmethod
    def get_stream_and_topic_name(cls, string: str) -> tuple[str, str | None] | None:
        """Extract the stream and the topic name from a string.

        Match the whole string and try to be smart:
           direct topic links: #**stream name>topic name**
                            -> (stream name, topic name)
           stream links: #**stream_name** -> (stream_name, None)

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
        There are four cases handled here:
            @**abc** -> abc, @_**abc** -> abc
        and
           @**abc|1234** -> abc, @_**abc|1234** -> abc
           or - if get_user_id is True -
           @**abc|1234** -> (abc, 1234), @_**abc|1234** -> (abc, 1234)
           @**abc** -> (abc, None), @_**abc** -> (abc, None)

        Leading/trailing whitespace is discarded.
        Return None if no match could be found.
        """
        result: list[str] | None = cls.get_captured_strings_from_pattern_or(
            [
                (cls._USER_MENTIONED_ID_CAPTURE, [1, 2]),
                (cls._USER_LINKED_ID_CAPTURE, [1, 2]),
                (cls._USER_MENTIONED_CAPTURE, [1]),
                (cls._USER_LINKED_CAPTURE, [1]),
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
    def get_reaction_emoji(string: str) -> str | None:
        """Extract the reaction emoji from a string.

        Match the whole string.
        There are two cases handled here:
           :<name>: -> <name>
           Leading/trailing whitespace is discarded.
        Return None if no match could be found.
        """
        result: re.Match[str] | None = Regex._EMOJI_AUTOCOMPLETED_CAPTURE.match(string.strip())
        return Regex.get_captured_string_from_match(result, 1)
