import re
import shlex
from importlib import import_module
from inspect import getmembers, isclass, ismodule
from itertools import repeat
import time
from typing import Any, Callable, Iterable, Type, TypeVar, Final


LOGGING_FORMAT: Final[str] = (
    "[%(levelname)-8s] %(module)-15s: %(message)s"  # (%(asctime)s) | %(threadName)-15s| | %(funcName)-15s
)

T = TypeVar("T")


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
