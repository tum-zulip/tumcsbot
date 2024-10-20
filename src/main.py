#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""TUM CS Bot - a generic Zulip bot.

This bot is currently especially intended for administrative tasks.
It supports several commands which can be written to the bot using
a private message or a message starting with @mentioning the bot.
"""

import argparse
import os
import sys
import logging
from time import sleep

from tumcsbot.tumcsbot import TumCSBot


def main() -> None:
    argument_parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    argument_parser.add_argument(
        "zuliprc",
        metavar="ZULIPRC",
        nargs=1,
        help="zuliprc file containing the bot's configuration",
    )
    argument_parser.add_argument(
        "db_path", metavar="DB_PATH", nargs=1, help="path to the bot's database"
    )
    argument_parser.add_argument(
        "-d", "--debug", action="store_true", help="debugging mode switch"
    )
    argument_parser.add_argument(
        "-l", "--logfile", help="use LOGFILE for logging output"
    )
    args: argparse.Namespace = argument_parser.parse_args()

    bot: TumCSBot | None = None
    try:
        bot = TumCSBot(
            zuliprc=args.zuliprc[0],
            db_path=args.db_path[0],
            debug=args.debug,
            logfile=args.logfile,
        )

        bot.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.exception(e)
        bot = None
        sleep(1)

    if bot is None or bot.restart:
        print("Received termination request. Restarting: " + str(sys.argv))
        if bot is not None:
            bot.stop()
        os.execv(sys.argv[0], sys.argv)
    else:
        print("Terminated.")


if __name__ == "__main__":
    main()
