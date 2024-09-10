"""
.. Terminal Utilities
"""

from __future__ import annotations

__all__ = (
    "ActiveTerminalSyncProcess",
    "TTY",
    "active_terminal_sync",
    "get_active_terminal",
    "NoMultiProcessSyncWarning",
)

import os
import sys
from collections.abc import Callable
from functools import wraps
from io import FileIO
from multiprocessing import Process, RLock as mp_RLock
from threading import RLock
from warnings import warn

from typing_extensions import ClassVar, ParamSpec, TypeVar

from .exceptions import TermImageUserWarning
from .utils import arg_value_error_msg, no_redecorate

OS_IS_UNIX: bool
try:
    import fcntl  # noqa: F401
    import termios  # noqa: F401
    from select import select  # noqa: F401
except ImportError:
    OS_IS_UNIX = False
else:
    OS_IS_UNIX = True


# Type Variables and Aliases
# ======================================================================================

P = ParamSpec("P")
T = TypeVar("T")


# Exceptions
# ======================================================================================


class NoMultiProcessSyncWarning(TermImageUserWarning):
    """Issued by :py:class:`~term_image.terminal.ActiveTerminalSyncProcess` when
    :py:mod:`multiprocessing.synchronize` is not supported on the host platform.
    """


# Decorator Functions
# ======================================================================================


@no_redecorate
def active_terminal_sync(func: Callable[P, T]) -> Callable[P, T]:
    """Synchronizes access to the :term:`active terminal`.

    Args:
        func: The function to be wrapped.

    When a decorated function is called, a re-entrant lock is acquired by the current
    process/thread and released after the call, such that any other decorated
    function called within another process/thread waits until the lock is fully
    released (i.e has been released as many times as acquired) by the owner
    process/thread.

    IMPORTANT:
        It only works across subprocesses (recursively) started directly or indirectly
        via :py:class:`~term_image.terminal.ActiveTerminalSyncProcess` (along with their
        parent processes) and all their threads, provided
        :py:mod:`multiprocessing.synchronize` is supported on the host platform.
    """

    @wraps(func)
    def active_terminal_access_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        if not (tty := get_active_terminal()):
            return func(*args, **kwargs)

        # If a thread reaches this point while the lock is being changed
        # (the old lock has been acquired but hasn't been changed), after the lock
        # has been changed and the former lock is released, the waiting thread will
        # acquire the old lock making it to be out of sync.
        # Hence the second expression, which allows such a thread to acquire the new
        # lock and be in sync.
        # NB: Multiple expressions are processed as nested with statements.
        with tty.lock, tty.lock:
            # _logger.debug(f"{func.__name__} acquired TTY lock", stacklevel=3)
            return func(*args, **kwargs)

    if func.__module__.startswith("term_image") and func.__doc__ is not None:
        sync_doc = """

        IMPORTANT:
            Synchronized with :py:func:`~term_image.terminal.active_terminal_sync`.
        """

        last_line = func.__doc__.rpartition("\n")[2]
        indent = " " * (len(last_line) - len(last_line.lstrip()))
        active_terminal_access_wrapper.__doc__ = func.__doc__.rstrip() + "\n".join(
            line.replace(" " * 8, indent, 1) for line in sync_doc.splitlines()
        )

    return active_terminal_access_wrapper


# Non-decorator Classes
# ======================================================================================


class ActiveTerminalSyncProcess(Process):
    """A process for :term:`active terminal` access synchronization

    This is a subclass of :py:class:`multiprocessing.Process` which provides support
    for synchronizing access to the :term:`active terminal` (via
    :py:func:`@active_terminal_sync <term_image.terminal.active_terminal_sync>`)
    across processes (the parent process and all its child processes started via an
    instance of this class, recursively).

    WARNING:
        If :py:mod:`multiprocessing.synchronize` is supported on the host platform
        and a subprocess is started (via an instance of **this class**) within a
        call to a function decorated with ``@active_terminal_sync``, the thread
        in which that occurs will be out of sync until the call (to the decorated
        function) returns.

        In short, avoid starting a subprocess (via an instance of **this class**)
        within a function decorated with ``@active_terminal_sync``.
    """

    _tty_sync_attempted: ClassVar[bool] = False
    _tty_synced: ClassVar[bool] = False

    _tty_name: str | None = None
    _tty_lock: RLock | None = None

    def start(self) -> None:
        """See :py:meth:`multiprocessing.Process.start`.

        Warns:
            NoMultiProcessSyncWarning: :py:mod:`multiprocessing.synchronize` is not
              supported on the host platform.
        """
        # Ensures the lock is not acquired by another thread before changing it.
        # The only case in which this is useless is when the owner thread is the one
        # starting the process. In such a situation, the owner thread will be partially
        # (may acquire the new lock in a nested call while still holding the old lock)
        # out of sync until it has fully released the old lock.

        if not (tty := get_active_terminal()):
            return super().start()

        with tty.lock:
            self._tty_name = tty.name

            if not ActiveTerminalSyncProcess._tty_sync_attempted:
                try:
                    self._tty_lock = tty.lock = mp_RLock()  # type: ignore[assignment]
                except ImportError:
                    warn(
                        "Multi-process synchronization is not supported on this "
                        "platform!\n"
                        "Hence, if any subprocess will be writing/reading to/from "
                        "the active terminal, it may be unsafe to perform terminal "
                        "queries.\n"
                        "See https://term-image.readthedocs.io/en/stable/guide"
                        "/concepts.html#terminal-queries\n",
                        NoMultiProcessSyncWarning,
                    )
                else:
                    ActiveTerminalSyncProcess._tty_synced = True
                ActiveTerminalSyncProcess._tty_sync_attempted = True
            elif ActiveTerminalSyncProcess._tty_synced:
                self._tty_lock = tty.lock

        super().start()

    def run(self) -> None:
        global _tty, _tty_determined

        if self._tty_name:
            if _tty:
                _tty.close()
            _tty = TTY(self._tty_name)
            _tty_determined = True

            if self._tty_lock:
                _tty.lock = self._tty_lock
                ActiveTerminalSyncProcess._tty_synced = True

            ActiveTerminalSyncProcess._tty_sync_attempted = True

        return super().run()


class TTY(FileIO):
    """A TTY[-like] device

    Args:
        fd_or_name: An open file descriptor connected to (or the filename of) a
          TTY[-like] device.

    Raises:
        ValueError: *fd_or_name* is not [connected to] a TTY[-like] device.

    See :py:class:`io.FileIO` for further description.
    """

    lock: RLock

    def __init__(
        self,
        fd_or_name: int | str | bytes,
        mode: str = "r+",
        closefd: bool = True,
        opener: Callable[[str | bytes, int], int] | None = None,
    ) -> None:
        super().__init__(fd_or_name, mode, closefd, opener)

        if not self.isatty():
            self.close()
            raise arg_value_error_msg(
                "'fd_or_name' is not [connected to] a TTY[-like] device", fd_or_name
            )

        self.lock = RLock()

    def __del__(self) -> None:
        del self.lock
        super().__del__()


# Non-decorator Functions
# ======================================================================================


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


# Variables
# ======================================================================================


_tty: TTY | None = None
_tty_determined: bool = False
