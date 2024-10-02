#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import os
import subprocess as sp
from inspect import cleandoc
from pathlib import Path
from typing import Any, Iterable

from tumcsbot.lib.response import Response
from tumcsbot.plugin import PluginCommand,Plugin
from tumcsbot.lib.conf import Conf

class Update(PluginCommand, Plugin):
    syntax = "update"
    description = cleandoc(
        """
        Update the bot. You may want to restart it afterwards.
        [only bot owner]
        """
    )
    _git_pull_cmd: list[str] = ["git", "pull"]
    _timeout: int = 15

    async def handle_message(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        if not Conf.is_bot_owner(message["sender_id"]):
            return Response.privilege_err(message)

        # Get the dirname of this file (which is located in the git repo).
        git_dir: Path = Path(__file__).parent.absolute()

        try:
            os.chdir(git_dir)
        except Exception as e:
            self.logger.exception(e)
            return Response.build_message(
                message,
                f"Cannot access the directory of my git repo {git_dir}. Please contact the admin.",
            )

        # Execute command and capture stdout and stderr into one stream (stdout).
        try:
            result: sp.CompletedProcess[Any] = sp.run(
                self._git_pull_cmd,
                stdout=sp.PIPE,
                stderr=sp.STDOUT,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except sp.TimeoutExpired:
            return Response.build_message(
                message,
                f"{self._git_pull_cmd} failed: timeout ({self._timeout} seconds) expired",
            )

        return Response.build_message(
            message,
            f"Return code: {result.returncode}\nOutput:\n```text\n{result.stdout}\n```",
        )
