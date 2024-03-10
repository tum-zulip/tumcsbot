from __future__ import annotations
from functools import wraps
from typing import AsyncGenerator, Callable, Any, Iterable, Protocol
from dataclasses import dataclass
from inspect import cleandoc

from tumcsbot.command_parser import CommandParser
from tumcsbot.lib import Response
from tumcsbot.plugin import (
    ArgConfig,
    CommandConfig,
    SubCommandConfig,
    OptConfig,
    PluginCommandMixin,
    Privilege,
    ZulipUser,
    ZulipUserNotFound,
)

class DMError(Exception):
    pass


@dataclass
class DMResponse:
    """
    Responds with a direct message to the sender.
    """

    message: str


@dataclass
class DMMessage:
    """
    Responds with a direct message to the sender.
    """

    to: ZulipUser
    message: str


@dataclass
class InlineResponse:
    """
    Responds with an inline message to the sender.
    """

    message: str


@dataclass
class ReactionResponse:
    """
    Reacts with an emote message to the sender.
    """

    emote: str


@dataclass
class PartialSuccess:
    """
    Indicates that the command was successful for a specific element dentoed by info.
    Can be used multiple times with yield.
    """

    info: str


@dataclass
class PartialError:
    """
    Indicates that the command was not successful for a specific element dentoed by info.
    Can be used multiple times with yield.
    """

    info: str


response_type = DMResponse | DMMessage | InlineResponse | ReactionResponse | PartialSuccess | PartialError | Response

command_func_type = Callable[
    [
        Any,
        ZulipUser,
        Any,
        CommandParser.Args,
        CommandParser.Opts,
        dict[str, Any],
    ],
    AsyncGenerator[response_type, None],
]

command_decorator_type = Callable[[command_func_type], command_func_type]

def get_meta(func: Any) -> SubCommandConfig:
    if not hasattr(func, "__tumsbot_plugin_meta__"):
        func.__tumsbot_plugin_meta__ = SubCommandConfig()
    if not isinstance(func.__tumsbot_plugin_meta__, SubCommandConfig):
        raise ValueError(
            f"Expected {func} to have a __tumsbot_plugin_meta__ attribute of type SubCommandConfig"
        )
    return func.__tumsbot_plugin_meta__


def arg(
    name: str,
    type: Callable[[Any], Any],
    description: str | None = None,
    privilege: Privilege | None = None,
    greedy: bool = False,
    optional: bool = False,
) -> command_decorator_type:
    if greedy and optional:
        raise ValueError("An argument cannot be both greedy and optional")

    def decorator(func: command_func_type) -> command_func_type:
        meta = get_meta(func)
        meta.args.insert(
            0, ArgConfig(name, type, description, privilege, greedy, optional)
        )

        @wraps(func)
        async def wrapper(
            self,
            sender: ZulipUser,
            session,
            args: CommandParser.Args,
            opts: CommandParser.Opts,
            message: dict[str, Any],
        ) -> AsyncGenerator[response_type, None]:
            if privilege is not None:  # and todo: check if option is present
                # todo: check privilege
                if not await sender.privileged:
                    raise UserNotPrivilegedException(
                        message, privilege, f"{self.plugin_name()} {get_meta(func).name}"
                    )

            if greedy and not optional:
                if len(getattr(args, name, [])) == 0:
                    # todo: better error message
                    raise DMError(
                        f"Error: At least one argument is required for `{name}`.",
                    )
            async for response in func(self, sender, session, args, opts, message):
                yield response

        return wrapper

    return decorator


def opt(
    opt: str,
    long_opt: str | None = None,
    type: Callable[[Any], Any] | None = None,
    description: str | None = None,
    privilege: Privilege | None = None,
) -> command_decorator_type:
    def decorator(func: command_func_type) -> command_func_type:
        meta = get_meta(func)
        meta.opts.insert(0, OptConfig(opt, long_opt, type, description, privilege))

        @wraps(func)
        async def wrapper(
            self,
            sender: ZulipUser,
            session,
            args: CommandParser.Args,
            opts: CommandParser.Opts,
            message: dict[str, Any],
        ) -> AsyncGenerator[response_type, None]:
            if privilege is not None:
                # todo: check privilege
                if not await sender.privileged:
                    raise UserNotPrivilegedException(
                        message, privilege, f"{self.plugin_name()} {get_meta(func).name}"
                    )
            
            async for response in func(self, sender, session, args, opts, message):
                yield response

        return wrapper

    return decorator


def privilege(privilege: Privilege) -> command_decorator_type:
    def decorator(func: command_func_type) -> command_func_type:
        meta = get_meta(func)
        meta.privilege = privilege

        @wraps(func)
        async def wrapper(
            self,
            sender: ZulipUser,
            session,
            args: CommandParser.Args,
            opts: CommandParser.Opts,
            message: dict[str, Any],
        ) -> AsyncGenerator[response_type, None]:
            if privilege is not None:
                if not await sender.privileged:
                    raise UserNotPrivilegedException(
                        message, privilege, f"{self.plugin_name()} {get_meta(func).name}"
                    )
            async for response in func(self, sender, session, args, opts, message):
                yield response

        return wrapper

    return decorator


class command:
    def __init__(self, fn: command_func_type | None = None, name: str | None = None):
        self.fn = fn
        self.name = name

        if name is None and fn is None:
            raise ValueError("name or function must be provided") 

        if name is None:
            self.name = fn.__name__

    def __call__(self, fn: command_func_type) -> command:
        self.fn = fn
        self.fn.__name__ = self.name
        return self

    @property
    def description(self) -> str | None:
        return cleandoc(self.fn.__doc__) if self.fn.__doc__ else None

    @property
    def syntax(self) -> str:
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
    def meta(self) -> SubCommandConfig:
        return get_meta(self.fn)

    @property
    def args(self) -> dict[str, Any]:
        return {
            arg.name: arg.type
            for arg in self.meta.args
            if not arg.greedy and not arg.optional
        }

    @property
    def opts(self) -> dict[str, Any]:
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
    def greedy(self) -> dict[str, Any]:
        return {arg.name: arg.type for arg in self.meta.args if arg.greedy}

    @property
    def optional_args(self) -> dict[str, Any]:
        return {arg.name: arg.type for arg in self.meta.args if arg.optional}

    def __set_name__(self, owner, _) -> None:

        if not issubclass(owner, PluginCommandMixin):
            raise TypeError(
                f"Command decorator can only be used on PluginCommandMixin subclasses. {owner} is not a subclass of PluginCommandMixin."
            )

        if len(owner._tumcs_bot_commands.subcommands) == 0:
            owner._tumcs_bot_commands = CommandConfig()
            owner._tumcs_bot_command_parser = CommandParser()

        self.meta.name = self.name
        self.meta.description = self.description

        owner._tumcs_bot_commands.subcommands.append(self.meta)
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
        async def wrapper(
            self,
            sender: ZulipUser,
            session,
            args: CommandParser.Args,
            opts: CommandParser.Opts,
            message: dict[str, Any],
        ) -> list[Response] | Iterable[Response] | Response:
            ZulipUser.set_client(self.client)
            self.logger.debug(
                "%s is calling `%s %s` with args %s and opts %s",
                message["sender_full_name"],
                self.plugin_name(),
                outer_self.name,
                args,
                opts,
            )
            responses = []
            successful = []
            errors = []
            try:
                async for response in outer_self.fn(self, sender, session, args, opts, message):
                    self.logger.debug("Collected Response: %s", response)
                    if isinstance(response, DMResponse):
                        self.logger.debug("Collected DMResponse: %s", response.message)
                        responses.append(
                            Response.build_message(
                                message,
                                content=response.message,
                            )
                        )
                    elif isinstance(response, InlineResponse):
                        self.logger.debug("Collected InlineResponse: %s", response.message)
                        responses.append(
                            Response.build_message(
                                message,
                                content=response.message,
                            )
                        )
                    elif isinstance(response, ReactionResponse):
                        self.logger.debug("Collected ReactionResponse: %s", response.emote)
                        responses.append(
                            Response.build_reaction(
                                message,
                                emoji=response.emote,
                            )
                        )
                    elif isinstance(response, DMMessage):
                        self.logger.debug(
                            "Collected DMMessage: %s to %s", response.message, response.to
                        )
                        responses.append(
                            Response.build_message(
                                None,
                                content=response.message,
                                msg_type="private",
                                to=[await response.to.id],
                            )
                        )
                    elif isinstance(response, PartialSuccess):
                        self.logger.debug("Collected PartialSuccess: %s", response.info)
                        successful.append(response.info)
                    elif isinstance(response, PartialError):
                        self.logger.debug("Collected PartialError: %s", response.info)
                        errors.append(response.info)
                    else:
                        responses.append(response)
            except StopIteration:
                pass
            except UserNotPrivilegedException as upe:
                return Response.privilege_excpetion(upe.message, upe.description)
            except ZulipUserNotFound as e:
                return Response.build_message(
                    message,
                    f"Error: {e}",
                )
            except DMError as e:
                return Response.build_message(
                    message,
                    str(e),
                )

            if len(errors) > 0:
                responses.append(
                    Response.build_message(
                        message,
                        "Error: "
                        + ", ".join(errors)
                        + "\nSuccess: "
                        + ", ".join(successful),
                    )
                )
            elif len(responses) == 0 and len(successful) > 0:
                responses.append(Response.ok(message))
            elif len(responses) == 0:
                responses.append(
                    Response.build_message(
                        message,
                        "Looks like there's nothing for me to do.",  # todo this is wrong
                    )
                )
            return responses

        # todo: idk if this is right
        wrapper._tumcsbot_meta = self.meta
        setattr(owner, self.name, wrapper)


class UserNotPrivilegedException(Exception):
    def __init__(self, msg, required_privilege: Privilege, command_name: str) -> None:
        text = cleandoc(
            """
             You don't have sufficient privileges to execute the command `{}`.
             This command requires at least {} rights.
 
            """
        )
        self._description = text.format(command_name, required_privilege)
        self._message = msg
        super().__init__(self._description)

    @property
    def message(self):
        return self._message

    @property
    def description(self):
        return self._description
