#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import unittest

from tumcsbot.lib.regex import Regex


class RegexTest(unittest.TestCase):
    emoji_names: list[tuple[str, str | None]] = [
        # emoji names like this are no longer supported: ("test", "test"),
        (":test:", "test"),
        (":tes:t:", None),
        ("test:", None),
        (":test", None),
    ]
    channel_names: list[tuple[str, str | None]] = [
        # channel names like this are no longer supported:  ("test", "test"),
        # channel names like this are no longer supported:  ("abc def", "abc def"),
        ('#**!/"§$& - ("!~EÜ**', '!/"§$& - ("!~EÜ'),
        ('!/"§$& - ("!~EÜ', None),
        ("#**test**", "test"),
        ("#*test*", None),
        ("#**test*", None),
        ("#*test**", None),
    ]
    user_names: list[tuple[str, str | None]] = [
        # usernames like this are no longer supported: ("John Doe", "John Doe"),
        # usernames like this are no longer supported: ("John", "John"),
        # usernames like this are no longer supported: ("John Multiple Doe", "John Multiple Doe"),
        ("@**John**", "John"),
        ("@_**John Doe**", "John Doe"),
        ("@*John*", None),
        ("@_*John*", None),
        ("@John**", None),
        ("@_John**", None),
        ("@**John", None),
        ("@_**John", None),
        ("Jo\\hn", None),
        ("@**J\\n**", None),
        ('@_**John D"e**', None),
    ]
    user_names_ids: list[tuple[str, tuple[str, int] | None]] = [
        ("@_**John Doe|123**", ("John Doe", 123)),
        ("@**John Doe|456**", ("John Doe", 456)),
        ("@John Doe|123**", None),
        ("@**John Doe|123", None),
    ]

    def test_emoji_names(self) -> None:
        for string, emoji in self.emoji_names:
            self.assertEqual(Regex.get_emoji_name(string), emoji, msg=f"String '{string}' was parsed as '{Regex.get_emoji_name(string)}' but should be '{emoji}'.")

    def test_channel_names(self) -> None:
        for string, channel_name in self.channel_names:
            self.assertEqual(Regex.get_channel_name(string), channel_name)

    def test_channel_and_topic_names(self) -> None:
        self.assertIsNone(Regex.get_channel_and_topic_name(""))
        self.assertEqual(Regex.get_channel_and_topic_name("abc"), ("abc", None))
        self.assertEqual(Regex.get_channel_and_topic_name("#**abc**"), ("abc", None))
        self.assertEqual(
            Regex.get_channel_and_topic_name("#**abc>def**"), ("abc", "def")
        )
        self.assertEqual(
            Regex.get_channel_and_topic_name("#**abc>def>ghi**"), ("abc", "def>ghi")
        )
        self.assertEqual(
            Regex.get_channel_and_topic_name("#**>**"), (">", None)
        )  # sadly, those are possible...
        self.assertEqual(Regex.get_channel_and_topic_name("#**>a**"), (">a", None))
        self.assertEqual(Regex.get_channel_and_topic_name("#**a>**"), ("a>", None))

    def test_user_names(self) -> None:
        for string, user_name in self.user_names:
            self.assertEqual(Regex.get_user_name(string), user_name)

    def test_user_names_ids(self) -> None:
        for string, user_name in self.user_names_ids:
            self.assertEqual(Regex.get_user_name(string, get_user_id=True), user_name)
