from typing import Any, Callable
from argparse import Namespace

import regex

from tumcsbot.lib import Regex

class CommandParser:
    """A simple shell-like command line parser.

    This command line parser can operate in two modes:
    1. Only parse positional arguments, no special treating for
       arguments preceded by "-".
    2. Additionally parse short options (preceded by "-").
       - Options always precede positional arguments.
       - Options can take an (optional) argument which has to directly
         follow the option character (no space(s) in-between).
       - Options cannot be grouped (such as in "ls -lah").
       - The order of the options does not matter.
       - Contrary to positional arguments (except the last one), options
         are always optional.
       - The preceding "-" can be escaped by two backslashes in order to
         prevent the following token to be considered as option.
    """

    class Args(Namespace):
        pass

    class Opts(Namespace):
        pass

    class IllegalCommandParserState(Exception):
        pass

    def __init__(self) -> None:
        self.commands: dict[
            str,
            tuple[
                dict[str, Callable[[str], Any] | None],
                dict[str, Callable[[str], Any]],
                dict[str, Callable[[str], Any]],
                dict[str, Callable[[str], Any]],
            ],
        ] = {}

    def add_subcommand(
        self,
        name: str,
        opts: dict[str, Callable[[str], Any] | None] = {},
        args: dict[str, Callable[[str], Any]] = {},
        optionals: dict[str, Callable[[str], Any]] = {},
        greedy: dict[str, Callable[[str], Any]] = {},
    ) -> bool:
        """Add a subcommand to the parser.

        Arguments:
        ----------
        name       The name of the subcommand to add.
                   In case that a subcommand with the given name has
                   already been added to this parser, the previous one
                   will be overwritten.
        opts       The options to be expected as dict mapping the
                   option name to the function that should be used
                   to parse the option argument string. The function
                   may be None if the option should be considered as
                   simple flag not accepting any argument. In this case,
                   a boolean value is associated with this option in the
                   return value of parse() which indicates whether the
                   flag has been present or not.
                   In case that the option should support an optional
                   parameter, the function has to be able to accept an
                   empty string as argument.
                   (default: {})
        args       The arguments to be expected as dict mapping the
                   argument name to the function that should be used
                   to get the argument value from the command string.
                   (default: {})
                   Note that the order matters! (Since 3.7, the
                   insertion order of dict keys is preserved, see
                   https://mail.python.org/pipermail/python-dev/
                   2017-December/151283.html)
        optionals  The optional arguments to be expected as dict mapping the
                   argument name to the function that should be used
                   to get the argument value from the command string.
                   (default: {})
        greedy     The greedy arguments that can consume more than one argument to be expected
                   as dict mapping the argument name to the function that should be used
                   to get the argument value from the command string.
                   (default: {})
        If the given arguments would lead to a broken state of the
        parser, an IllegalCommandParserState exception is thrown.
        """
        if not name:
            raise self.IllegalCommandParserState()

        self.commands[name] = (opts, args, optionals, greedy)
        return True

    @staticmethod
    def strip_quotes(s: str) -> str:
        s = s.strip()
        if s.startswith("'") and s.endswith("'"):
            return s[1:-1].strip()
        if s.startswith('"') and s.endswith('"'):
            return s[1:-1].strip()
        return s

    def parse(self, command: str | None) -> tuple[str, Opts, Args]:
        """Parse the given command string.

        Return the parsed subcommand together with its options and
        arguments. When reorder is True, arguments are matched to the argument based on structure and name instead of order.
        """
        result_opts: tuple[dict[str, Any], list[str]] | None

        if not command:
            raise CommandParser.IllegalCommandParserState("No command given")
        
        if not self.commands:
            raise CommandParser.IllegalCommandParserState("No subcommands specified that can be parsed.")
        
        # Split on tokens.

        matches_opt: regex.regex.Match[str] | None = Regex._ARGUMENT_PATTERN.match(
            command
        )
        if not matches_opt:
            raise CommandParser.IllegalCommandParserState(f"`{command}` is not a valid pattern")

        matches: regex.regex.Match[str] = matches_opt
        try:
            tokens: list[str] | None = [
                bytes(e, "Latin-1").decode("unicode-escape")
                for e in [
                    CommandParser.strip_quotes(e)
                    for e in matches.capturesdict()["args"]
                ]
                if e
            ]
        except Exception as e:
            raise CommandParser.IllegalCommandParserState(f"`{command}` is not a valid pattern") from e
        
        if not tokens or len(tokens) == 0:
            raise CommandParser.IllegalCommandParserState(f"`{command}` is not a valid pattern")
        
        # Get the fitting subcommand.
        subcommand: str = tokens[0]
        if subcommand not in self.commands:
            raise CommandParser.IllegalCommandParserState(
                f"Subcommand `{subcommand}` not found."
            )

        opts, positional, optional, greedy = self.commands[subcommand]

        optiones_first = []
        arguments_last = []

        optarg = False
        for t in tokens[1:]:
            if optarg:
                optiones_first.append(t)
                optarg = False
            elif t.startswith("-") and not t.startswith("--"):
                optiones_first.append(t)
                opt: str = t.lstrip(" -")
                if opt in opts and opts[opt] is not None:
                    optarg = True
            else:
                arguments_last.append(t)

        result_opts = self._parse_opts(opts, optiones_first + arguments_last)

        options = result_opts[0]
        match_result = CommandParser._match_arguments(
            positional, optional, greedy, result_opts[1]
        )

        matched_args, remainder = match_result

        if len(remainder) > 0:
            raise CommandParser.IllegalCommandParserState(
                f"Too many arguments for subcommand `{subcommand}`. Remaining: {remainder}."
            )
        return (subcommand, self.Opts(**options), self.Args(**matched_args))

    @staticmethod
    def _convert_argument(
        target_name: str,
        arg: str,
        converter: Callable[[str], Any],
        solution: dict[str, Any],
    ) -> bool:
        try:
            value = converter(arg)
        except Exception:
            return False
        if target_name in solution and isinstance(solution[target_name], type([])):
            solution[target_name].append(value)
        elif target_name not in solution or solution[target_name] is None:
            solution.update({target_name: value})
        else:
            return False
        return True

    @staticmethod
    def _match_argument_to_target(
        target: dict[str, Any],
        arg: str,
        solution: dict[str, Any],
    ) -> bool:
        for target_name, converter in target.items():
            if CommandParser._convert_argument(target_name, arg, converter, solution):
                return True
        return False

    @staticmethod
    def _match_arguments(
        positional: dict[str, Any],
        optional: dict[str, Any],
        greedy: dict[str, Any],
        args: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        solution: dict[str, Any] = {}
        for key in optional:
            solution.update({key: None})
        for key in greedy:
            solution.update({key: []})

        remainder = {i: a for i, a in enumerate(args)}

        for i, arg in enumerate(args):
            if CommandParser._match_argument_to_target(positional, arg, solution):
                remainder.pop(i)
                continue
            elif CommandParser._match_argument_to_target(optional, arg, solution):
                remainder.pop(i)
                continue
            elif CommandParser._match_argument_to_target(greedy, arg, solution):
                remainder.pop(i)
                continue

        for key in positional:
            if key not in solution:
                raise CommandParser.IllegalCommandParserState(
                    f"Positional argument `{key}` not found."
                )

        return solution, list(remainder.values())

    def _parse_opts(
        self,
        opts: dict[str, Callable[[Any], Any] | None],
        tokens: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        """Parse options from tokens.

        Return the parsed options together with their converted
        arguments and the non-option tokens.
        Return None on error.
        """
        index: int = 0
        result: dict[str, Any] = {}
        token: str = ""

        opts_len: int = len(opts)
        if not opts_len:
            return ({}, tokens)

        skip_next_token = False
        for index in range(len(tokens)):
            if skip_next_token:
                skip_next_token = False
                continue
            token = tokens[index]
            # Stop at the first non-option token.
            if token[0] != "-":
                break

            opt: str
            if token.startswith("-") and not token.startswith("--"):
                opt = token[1]
            else:
                opt = token[2:]

            if not opt in opts:
                # Invalid option.
                raise CommandParser.IllegalCommandParserState(
                    f"Invalid option `{opt}` for subcommand."
                )
            try:
                converter: Callable[[Any], Any] | None = opts[opt]
                if converter is not None:
                    optarg: str | None
                    if token.startswith("-") and not token.startswith("--"):
                        optarg = token[2:]
                    else:
                        optarg = tokens[index + 1] if index + 1 < len(tokens) else None
                        skip_next_token = True
                    result[opt] = converter(optarg)

                else:
                    result[opt] = True
            except Exception as e:
                raise CommandParser.IllegalCommandParserState(
                    f"Could not parse option `{opt}` for subcommand."
                ) from e 

        # Skip last option if there have been only options.
        if token and token[0] == "-":
            index += 1

        # Mark all non-existant flags as False and fill the values of
        # all the other options which were not specified on the given
        # command line with None.
        for opt in opts:
            if opt not in result:
                result[opt] = False if opts[opt] is None else None

        # Remove all backslash escapes for "-".
        # Note that split() in self.parse() already converted the two
        # backslashes to a single one!
        return (result, [t[1:] if t[0:2] == r"\-" else t for t in tokens[index:]])

