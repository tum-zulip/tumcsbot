from __future__ import annotations
from argparse import Namespace
from functools import wraps
from typing import AsyncGenerator, Callable, Any, Iterable
from inspect import cleandoc

import sqlalchemy

from tumcsbot.lib.db import Session
from tumcsbot.lib.response import Response
from tumcsbot.plugin import PluginCommandMixin
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.types import (
    ZulipChannelNotFound,
    ZulipUser,
    Privilege,
    response_type,
    SubCommandConfig,
    ArgConfig,
    OptConfig,
    CommandConfig,
    arg_type,
    command_func_type,
    command_decorator_type,
    DMError,
    UserNotPrivilegedException,
    ZulipUserNotFound,
    DMMessage,
    DMResponse,
    InlineResponse,
    ReactionResponse,
    PartialSuccess,
    PartialError,
)


def get_meta(func: Any) -> SubCommandConfig:
    if not hasattr(func, "__tumsbot_plugin_meta__"):
        func.__tumsbot_plugin_meta__ = SubCommandConfig()
    if not isinstance(func.__tumsbot_plugin_meta__, SubCommandConfig):
        raise ValueError(
            f"Expected {func} to have a __tumsbot_plugin_meta__ attribute of type SubCommandConfig"
        )
    return func.__tumsbot_plugin_meta__


def to_python_type(ty: type) -> Any:
    """Convert a SQLAlchemy InstrumentedAttribute (Column Type) to a Python type or return the original type if it is not a SQLAlchemy Column type."""
    if isinstance(ty, sqlalchemy.orm.InstrumentedAttribute):
        columns = ty.property.columns
        if len(columns) != 1:
            raise ValueError(f"Expected exactly one column, got {len(columns)}")

        column = columns[0]
        return column.type.python_type

    return ty


async def process_arg(
    name: str,
    greedy: bool,
    optional: bool,
    ty: Any,
    args: CommandParser.Args,
    session: Session,
) -> None:
    if greedy and not optional:
        if len(getattr(args, name, [])) == 0:
            # todo: better error message
            raise DMError(
                f"Error: At least one argument is required for `{name}`.",
            )

    async def handle_argument(value) -> Any:
        if isinstance(ty, sqlalchemy.orm.InstrumentedAttribute):
            obj = session.query(ty.class_).filter(ty == value).first()
            if not optional and obj is None:
                raise DMError(
                    f"Uuups, it looks like i could not find any {ty.class_.__name__} associated with `{value}` :botsceptical:"
                )
        else:
            obj = value

        if hasattr(obj.__class__, "__await__"):
            await obj # type: ignore
        return obj

    if greedy:
        result = []
        for value in getattr(args, name):
            result.append(await handle_argument(value))
    else:
        result = await handle_argument(getattr(args, name))
    setattr(args, name, result)


def arg(
    name: str,
    ty: arg_type,
    description: str | None = None,
    privilege: Privilege | None = None,
    greedy: bool = False,
    optional: bool = False,
) -> command_decorator_type:
    def decorator(func: command_func_type) -> command_func_type:
        meta = get_meta(func)
        if greedy and meta.args:
            raise ValueError("Greedy argument must be the last argument.")

        python_type = to_python_type(ty)
        meta.args.insert(
            0, ArgConfig(name, python_type, description, privilege, greedy, optional)
        )

        @wraps(func)
        async def wrapper(
            self,
            sender: ZulipUser,
            session: Session,
            args: CommandParser.Args,
            opts: CommandParser.Opts,
            message: dict[str, Any],
        ) -> AsyncGenerator[response_type, None]:
            if privilege is not None:  # and todo: check if option is present
                if not await sender.isPrivileged:
                    raise UserNotPrivilegedException()

            await process_arg(name, greedy, optional, ty, args, session)

            # todo: does yield from work here?
            async for response in func(self, sender, session, args, opts, message):
                yield response

        return wrapper

    return decorator


def opt(
    opt: str,
    long_opt: str | None = None,
    ty: arg_type | None = None,
    description: str | None = None,
    priv: Privilege | None = None,
) -> command_decorator_type:
    def decorator(func: command_func_type) -> command_func_type:
        meta = get_meta(func)
        python_type = to_python_type(ty)
        meta.opts.insert(
            0, OptConfig(opt, long_opt, python_type, description, priv)
        )

        @wraps(func)
        async def wrapper(
            self,
            sender: ZulipUser,
            session: Session,
            args: CommandParser.Args,
            opts: CommandParser.Opts,
            message: dict[str, Any],
        ) -> AsyncGenerator[response_type, None]:
            if priv is not None and getattr(opts, opt, None):
                if not sender.isPrivileged:
                    raise UserNotPrivilegedException(
                        f"Option `-{opt}` requires privilege *{priv.name}* :botsweat:"
                    )

            opt_value = getattr(opts, opt, None)
            long_opt_value = None
            if long_opt:
                long_opt_value = getattr(opts, long_opt, None)

            if opt_value and long_opt_value:
                raise DMError(
                    f"Error: Cannot use both short and long options for `{opt}`"
                )

            if long_opt and opt_value:
                setattr(opts, long_opt, opt_value)
            elif long_opt:
                setattr(opts, opt, long_opt_value)

            if ty and opt_value:
                await process_arg(opt, False, False, ty, opts, session)

            if long_opt:
                setattr(opts, long_opt, getattr(opts, opt, None))

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
            session: Session,
            args: CommandParser.Args,
            opts: CommandParser.Opts,
            message: dict[str, Any],
        ) -> AsyncGenerator[response_type, None]:
            if privilege is not None:
                if not sender.isPrivileged:
                    raise UserNotPrivilegedException(
                        f"You need to have *{privilege.name}* privilege to run this command. :botsweat:"
                    )
            async for response in func(self, sender, session, args, opts, message):
                yield response

        return wrapper

    return decorator


class command:
    def __init__(self, fn: command_func_type | None = None, name: str | None = None):
        self.fn = fn
        self._name = name

        if name is None and fn is None:
            raise ValueError("name or function must be provided")

        if name is None:
            self._name = fn.__name__

    def __call__(self, fn: command_func_type) -> command:
        self.fn = fn
        self.fn.__name__ = self.name
        return self

    @property
    def name(self) -> str:
        if self._name is None:
            raise ValueError("name is not set")
        return self._name

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
            arg.name: arg.ty
            for arg in self.meta.args
            if not arg.greedy and not arg.optional
        }

    @property
    def opts(self) -> dict[str, Any]:
        opts = {opt.opt: opt.ty for opt in self.meta.opts}

        opts.update(
            {
                opt.long_opt: opt.ty
                for opt in self.meta.opts
                if opt.long_opt is not None
            }
        )

        return opts

    @property
    def greedy(self) -> dict[str, Any]:
        return {arg.name: arg.ty for arg in self.meta.args if arg.greedy}

    @property
    def optional_args(self) -> dict[str, Any]:
        return {arg.name: arg.ty for arg in self.meta.args if arg.optional}

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
            session: Session,
            args: CommandParser.Args,
            opts: CommandParser.Opts,
            message: dict[str, Any],
        ) -> list[Response] | Iterable[Response] | Response:
            self.logger.info(
                "%s calls %s with %s and %s",
                sender.mention_silent,
                self.plugin_name(),
                args,
                opts,
            )
            responses = []
            successful = []
            errors = []
            try:
                async for response in outer_self.fn(
                    self, sender, session, args, opts, message
                ):
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
                        self.logger.debug(
                            "Collected InlineResponse: %s", response.message
                        )
                        responses.append(
                            Response.build_message(
                                message,
                                content=response.message,
                            )
                        )
                    elif isinstance(response, ReactionResponse):
                        self.logger.debug(
                            "Collected ReactionResponse: %s", response.emote
                        )
                        responses.append(
                            Response.build_reaction(
                                message,
                                emoji=response.emote,
                            )
                        )
                    elif isinstance(response, DMMessage):
                        self.logger.debug(
                            "Collected DMMessage: %s to %s",
                            response.message,
                            response.to,
                        )
                        await response.to
                        responses.append(
                            Response.build_message(
                                None,
                                content=response.message,
                                msg_type="private",
                                to=[response.to.id],
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
            except UserNotPrivilegedException as e:
                return Response.privilege_excpetion(
                    message, f"{self.plugin_name()} {outer_self.name}", str(e)
                )
            except ZulipUserNotFound as e:
                self.logger.exception(e)
                return Response.build_message(
                    message,
                    f"Error: {e}",
                )
            except ZulipChannelNotFound as e:
                self.logger.exception(e)
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
                # Handling multiple errors
                if len(errors) > 1:
                    error_message = "Multiple errors occurred: " + ", ".join(errors)
                else:
                    # Handling a single error
                    error_message = f"An error occurred: {errors[0]}"

                if len(successful) > 0:
                    error_message + "\nHowever, the following were successful: " + ", ".join(
                        successful
                    ),

                # This case covers both multiple errors with no success, and a single error with no success
                responses.append(Response.build_message(message, error_message))

            elif len(responses) == 0 and len(successful) > 0:
                responses.append(Response.ok(message))
            elif len(responses) == 0:
                responses.append(
                    Response.build_message(
                        message,
                        "It looks like there's nothing for me to do.",  # Corrected response for clarity
                    )
                )
            return responses

        async def invoke(sender: ZulipUser, session: Session, message: dict[str, Any], **kwargs: Any) -> AsyncGenerator[response_type, None]:
            args_ns = CommandParser.Args(
                **{arg.name: kwargs.get(arg.name) for arg in self.meta.args}
            )
            opts_names = zip(
                [opt.opt for opt in self.meta.opts],
                [opt.long_opt for opt in self.meta.opts if opt.long_opt],
            )
            opts_dict = {s: kwargs.get(s) or kwargs.get(l) for s, l in opts_names}
            opts_dict.update({l: kwargs.get(l) or kwargs.get(s) for s, l in opts_names})
            opts_ns = CommandParser.Opts(**opts_dict)
            async for response in outer_self.fn(
                self, sender, session, args_ns, opts_ns, message
            ):
                yield response

        # todo: idk if this is right
        setattr(wrapper, "_tumcsbot_meta", self.meta)
        setattr(wrapper, "invoke", invoke)
        setattr(wrapper, "__parent_class__", owner)
        setattr(owner, self.name, wrapper)
