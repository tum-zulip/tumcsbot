from functools import wraps
import logging
from typing import Callable, Any, Iterable
from enum import Enum
from dataclasses import dataclass, field
from inspect import cleandoc

from tumcsbot.command_parser import CommandParser
from tumcsbot.lib import Response
from tumcsbot.plugin import ArgConfig, CommandConfig, OptConfig, PluginCommandMixin, Privilege
    


def get_meta(func) -> CommandConfig:
    if not hasattr(func, "__tumsbot_plugin_meta__"):
        func.__tumsbot_plugin_meta__ = CommandConfig()
    return func.__tumsbot_plugin_meta__


def arg(
    name: str,
    type: Callable[[Any], Any],
    description: str | None = None,
    privilege: Privilege | None = None,
    greedy: bool = False,
    optional: bool = False,
):
    if greedy and optional:
        raise ValueError("An argument cannot be both greedy and optional")

    def decorator(func):
        meta = get_meta(func)
        meta.args.append(
            ArgConfig(name, type, description, privilege, greedy, optional)
        )

        @wraps(func)
        def wrapper(
            self,
            message: dict[str, Any],
            args: CommandParser.Args,
            opts: CommandParser.Opts,
        ) -> Response | Iterable[Response]:
            if privilege is not None:  # and todo: check if option is present
                # todo: check privilege
                if not self.client.user_is_privileged(
                    message["sender_id"],
                    allow_moderator=privilege == Privilege.MODERATOR,
                ):
                    return Response.privilege_err_command(
                        message, f"{self.plugin_name()} {func.__name__}"
                    )
            
            if greedy and not optional:
                if len(args[name]) == 0:
                    # todo: better error message
                    return Response.build_message(
                        message,
                        f"Error: At least one argument is required for `{name}`.",
                    )
            return func(self, message, args, opts)

        return wrapper

    return decorator


def opt(
    opt: str,
    long_opt: str | None = None,
    type: Callable[[Any], Any] | None = None,
    description: str | None = None,
    privilege: Privilege | None = None,
):
    def decorator(func):
        meta = get_meta(func)
        meta.opts.append(OptConfig(opt, long_opt, type, description, privilege))

        @wraps(func)
        def wrapper(
            self,
            message: dict[str, Any],
            args: CommandParser.Args,
            opts: CommandParser.Opts,
        ) -> Response | Iterable[Response]:
            if privilege is not None:
                # todo: check privilege
                if not self.client.user_is_privileged(
                    message["sender_id"],
                    allow_moderator=privilege == Privilege.MODERATOR,
                ):
                    return Response.privilege_err_command(
                        message, f"{self.plugin_name()} {func.__name__}"
                    )
                pass
            return func(self, message, args, opts)

        return wrapper

    return decorator


def privilege(privilege: Privilege):
    def decorator(func):
        meta = get_meta(func)
        meta.privilege = privilege

        @wraps(func)
        def wrapper(
            self,
            message: dict[str, Any],
            args: CommandParser.Args,
            opts: CommandParser.Opts,
        ) -> Response | Iterable[Response]:
            if privilege is not None:
                if not self.client.user_is_privileged(
                    message["sender_id"],
                    allow_moderator=privilege == Privilege.MODERATOR,
                ):
                    return Response.privilege_err_command(
                        message, f"{self.plugin_name()} {func.__name__}"
                    )
            return func(self, message, args, opts)

        return wrapper

    return decorator


class command:
    def __init__(self, fn=None, name=None):
        self.fn = fn
        self.name = name

        if name is None:
            self.name = fn.__name__

    def __call__(self, fn) -> Any:
        self.fn = fn
        self.fn.__name__ = self.name
        return self

    @property
    def description(self):
        return (
            cleandoc(self.fn.__doc__)
            if self.fn.__doc__
            else "No description available."
        )

    @property
    def syntax(self):
        optarg: Callable[[str], str] = lambda x: (
            " arg" if x in self.opts and self.opts[x] is not None else ""
        )

        optprefix: Callable[[str], str] = lambda x: "-" if len(x) == 1 else "--"
        options_strs = [f"[{optprefix(o)}{o}{optarg(o)}]" for o in self.opts]
        optional_strs = [f"[{o}]" for o in self.optional_args]
        arg_strs = [f"<{p}>" for p in self.args]
        greedy_strs = [f"[{g}...]" for g in self.greedy]

        args_str = " ".join(options_strs + optional_strs + arg_strs + greedy_strs)
        if len(args_str) > 0:
            return f"{self.name} {args_str}"
        return f"{self.name}"

    @property
    def meta(self):
        return get_meta(self.fn)

    @property
    def args(self):
        return {
            arg.name: arg.type
            for arg in self.meta.args
            if not arg.greedy and not arg.optional
        }

    @property
    def opts(self):
        opts = {opt.opt: opt.type for opt in self.meta.opts}

        opts.update(
            {
                opt.long_opt: opt.type
                for opt in self.meta.opts
                if opt.long_opt is not None
            }
        )

        return opts

    @property
    def greedy(self):
        return {arg.name: arg.type for arg in self.meta.args if arg.greedy}

    @property
    def optional_args(self):
        return {arg.name: arg.type for arg in self.meta.args if arg.optional}

    def __set_name__(self, owner, name):

        if not issubclass(owner, PluginCommandMixin):
            raise TypeError(
                f"Command decorator can only be used on PluginCommandMixin subclasses. {owner} is not a subclass of PluginCommandMixin."
            )
        
        if len(owner._tumcs_bot_commands) == 0:
            owner._tumcs_bot_commands = []
            owner._tumcs_bot_command_parser = CommandParser()
        
        self.meta.name = self.name
        owner._tumcs_bot_commands.append(self.meta)
        command_parser = owner._tumcs_bot_command_parser

        command_parser.add_subcommand(
            self.name,
            args=self.args,
            opts=self.opts,
            optionals=self.optional_args,
            greedy=self.greedy,
        )

        # replace ourself with the original method
        outer_self = self

        @wraps(self.fn)
        def wrapper(
            self,
            message: dict[str, Any],
            args: CommandParser.Args,
            opts: CommandParser.Opts,
        ) -> Response | Iterable[Response]:
            self.logger.debug(
                "%s is calling `%s %s` with args %s and opts %s",
                message["sender_full_name"],
                self.plugin_name(),
                outer_self.name,
                args,
                opts,
            )
            return outer_self.fn(self, message, args, opts)

        # todo: idk if this is right
        wrapper._tumcsbot_meta = self.meta
        wrapper._tumcsbot_syntax = self.syntax
        wrapper._tumcsbot_description = self.description
        setattr(owner, self.name, wrapper)
