"""
.. Terminal Utilities
"""

from __future__ import annotations

__all__ = ("TTY",)

from collections.abc import Callable
from io import FileIO

from .utils import arg_value_error_msg


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
