"""
.. Terminal Utilities
"""

from __future__ import annotations

__all__ = ("TTY", "get_active_terminal")

import os
import sys
from collections.abc import Callable
from io import FileIO

from .utils import arg_value_error_msg

OS_IS_UNIX: bool
try:
    import fcntl  # noqa: F401
    import termios  # noqa: F401
    from select import select  # noqa: F401
except ImportError:
    OS_IS_UNIX = False
else:
    OS_IS_UNIX = True


class TTY(FileIO):
    """A TTY[-like] device

    Args:
        fd_or_name: An open file descriptor connected to (or the filename of) a
          TTY[-like] device.

    Raises:
        ValueError: *fd_or_name* is not [connected to] a TTY[-like] device.

    See :py:class:`io.FileIO` for further description.
    """

    def __init__(
        self,
        fd_or_name: int | str | bytes,
        mode: str = "r+",
        closefd: bool = True,
        opener: Callable[[str | bytes, int], int] | None = None,
    ):
        super().__init__(fd_or_name, mode, closefd, opener)

        if not self.isatty():
            self.close()
            raise arg_value_error_msg(
                "'fd_or_name' is not [connected to] a TTY[-like] device", fd_or_name
            )


def get_active_terminal() -> TTY | None:
    """Determines the :term:`active terminal`.

    Returns:
        - the :py:class:`TTY` instance associated with the :term:`active terminal`, OR
        - ``None``, on non-unix-like platforms or when there is no
          :term:`active terminal`.
    """
    global _tty, _tty_determined

    if _tty_determined:
        return _tty

    _tty_determined = True

    if not OS_IS_UNIX:
        return None

    for stream in ("out", "in", "err"):  # In order of priority
        try:
            # A new file descriptor is required because both read and write access
            # are required and in case the file descriptor is accidental closed.
            tty_name = os.ttyname(getattr(sys, f"__std{stream}__").fileno())
            break
        except (OSError, AttributeError):
            pass
    else:
        tty_name = "/dev/tty"

    try:
        _tty = TTY(tty_name)
    except OSError:
        return None

    return _tty


_tty: TTY | None = None
_tty_determined: bool = False
