"""
.. Terminal Utilities
"""

from __future__ import annotations

__all__ = (
    "ActiveTerminalSyncProcess",
    "TTY",
    "get_active_terminal",
    "with_active_terminal_lock",
    "TerminalError",
    "NoActiveTerminalError",
    "TTYError",
    "NoMultiProcessSyncWarning",
)

import os
import sys
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from functools import wraps
from io import FileIO
from multiprocessing import Process, RLock as mp_RLock
from threading import RLock
from time import monotonic
from warnings import warn

from typing_extensions import Buffer, ClassVar, ParamSpec, TypeVar, overload

from .exceptions import TermImageError, TermImageUserWarning
from .utils import arg_value_error_msg, arg_value_error_range, no_redecorate

OS_IS_UNIX: bool
try:
    import fcntl  # noqa: F401
    import termios
    from select import select
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


class TerminalError(TermImageError):
    """Base exception class for errors specific to :py:mod:`Terminal Utilities
    <term_image.terminal>`.
    """


class NoActiveTerminalError(TerminalError):
    """Raised when there is no :term:`active terminal`."""


class TTYError(TerminalError):
    """Raised for errors specific to :py:class:`~term_image.terminal.TTY`."""


class NoMultiProcessSyncWarning(TermImageUserWarning):
    """Issued by :py:class:`~term_image.terminal.ActiveTerminalSyncProcess` when
    :py:mod:`multiprocessing.synchronize` is not supported on the host platform.
    """


# Decorator Functions
# ======================================================================================


@no_redecorate
def with_active_terminal_lock(func: Callable[P, T]) -> Callable[P, T]:
    """Synchronizes access to the :term:`active terminal`.

    When a decorated function is called, a re-entrant lock is acquired by the
    current process/thread and released after the call, such that any other
    decorated function called within another process/thread waits until the lock
    is fully released (i.e has been released as many times as acquired) by the
    owner process/thread.

    IMPORTANT:
        It only works across subprocesses (recursively) started directly or
        indirectly via :py:class:`~term_image.terminal.ActiveTerminalSyncProcess`
        (along with their parent processes) and all their threads, provided
        :py:mod:`multiprocessing.synchronize` is supported on the host platform.
    """

    @wraps(func)
    def with_active_terminal_lock_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            tty = get_active_terminal()
        except NoActiveTerminalError:
            return func(*args, **kwargs)

        with tty.lock():
            # _logger.debug(f"{func.__name__} acquired TTY lock", stacklevel=3)
            return func(*args, **kwargs)

    if func.__module__.startswith("term_image") and func.__doc__ is not None:
        sync_doc = """

        IMPORTANT:
            Synchronized with :deco:`~term_image.terminal.with_active_terminal_lock`.
        """

        last_line = func.__doc__.rpartition("\n")[2]
        indent = " " * (len(last_line) - len(last_line.lstrip()))
        with_active_terminal_lock_wrapper.__doc__ = func.__doc__.rstrip() + "\n".join(
            line.replace(" " * 8, indent, 1) for line in sync_doc.splitlines()
        )

    return with_active_terminal_lock_wrapper


# Non-decorator Classes
# ======================================================================================


class ActiveTerminalSyncProcess(Process):
    """A process that enables cross-process :term:`active terminal` access
    synchronization

    This is a subclass of :py:class:`multiprocessing.Process` which provides support
    for synchronizing access to the :term:`active terminal`
    (via :deco:`~term_image.terminal.with_active_terminal_lock`)
    across processes (a parent process and all its child processes started via an
    instance of this class, recursively).

    WARNING:
        If :py:mod:`multiprocessing.synchronize` is supported on the host platform
        and a subprocess is started (via an instance of **this class**) within a
        call to a function decorated with
        :deco:`~term_image.terminal.with_active_terminal_lock`, the thread
        in which that occurs will be out of sync until the call (to the decorated
        function) returns.

        In short, avoid starting a subprocess (via an instance of **this class**)
        within a function decorated with
        :deco:`~term_image.terminal.with_active_terminal_lock`.
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
        try:
            tty = get_active_terminal()
        except NoActiveTerminalError:
            return super().start()

        # Ensures the lock is not acquired by another thread before changing it.
        # The only case in which this is useless is when the owner thread is the one
        # starting the process. In such a situation, the owner thread will be partially
        # (may acquire the new lock in a nested call while still holding the old lock)
        # out of sync until it has fully released the old lock.
        with tty._lock:
            self._tty_name = tty.name

            if not ActiveTerminalSyncProcess._tty_sync_attempted:
                try:
                    self._tty_lock = tty._lock = mp_RLock()  # type: ignore[assignment]
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
                self._tty_lock = tty._lock

        super().start()

    def run(self) -> None:
        global _tty, _tty_determined

        if self._tty_name:
            if _tty:
                _tty.close()
            _tty = TTY(self._tty_name)
            _tty_determined = True

            if self._tty_lock:
                _tty._lock = self._tty_lock
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

    _lock: RLock

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

        self._lock = RLock()

    def __del__(self) -> None:
        del self._lock
        super().__del__()

    @contextmanager
    def lock(self) -> Generator[None]:
        """Helps to synchronize access to the device.

        Returns:
            A context manager which, upon context entry, acquires a re-entrant lock
            unique to the instance and releases it upon exit.

        :rtype: contextlib.ContextDecorator

        TIP:
            The return value also doubles as a function decorator, as in::

                @tty.lock()
                def function():
                    ...

            in which case the lock is acquired whenever ``function()`` is called,
            and relased upon return.
        """
        with ExitStack() as context_stack:
            # For the **active terminal**...
            #
            # If a thread reaches this point while the lock is being changed
            # (the old lock has been acquired but hasn't been changed, in another
            # thread), after the lock has been changed and the old lock is released,
            # the waiting thread will acquire the old lock making it to be out of sync.
            context_stack.enter_context(self._lock)

            if self is _tty:
                # Hence, this second expression, which allows such a thread to
                # acquire the new lock and be in sync.
                context_stack.enter_context(self._lock)

            yield

    def query(
        self,
        request: Buffer,
        timeout: int | float = 0.1,
        no_more: Callable[[bytearray, int, int], bool] = lambda *_: False,
    ) -> bytes:
        """Writes to the device and reads from it afterwards.

        Args:
            request: The bytes to be written.

        Returns:
            The bytes read after writing *request* (empty, if no bytes were read
            before *timeout* elapsed); the "response".

        See :py:meth:`read_raw` for the descriptions of the remaining parameters.

        IMPORTANT:
            Some restrictions may be placed on *request* (if mutable) during the call
            e.g. a `bytearray` cannot be resized.

        ATTENTION:
            Any unread bytes are discarded before writing *request*. If such bytes
            might be needed, they can be read using :py:meth:`read_available` before
            calling this method.
        """
        request = memoryview(request)

        tty_fd = self.fileno()
        old_attr = termios.tcgetattr(tty_fd)
        new_attr = termios.tcgetattr(tty_fd)
        new_attr[3] &= ~termios.ECHO  # Disable input echo

        try:
            termios.tcsetattr(tty_fd, termios.TCSAFLUSH, new_attr)

            bytes_written = 0
            while bytes_written < request.nbytes:
                bytes_written += self.write(request[bytes_written:])

            return self.read_raw(timeout, no_more=no_more)
        finally:
            request.release()
            termios.tcsetattr(tty_fd, termios.TCSANOW, old_attr)

    def read_available(self) -> bytes:
        """Reads all **available** bytes without blocking.

        Returns:
            The bytes read (empty, if no bytes were readily available).
        """
        return self.read_raw()

    @overload
    def read_raw(
        self,
        timeout: int | float | None = ...,
        minimum: int = ...,
        maximum: int | None = ...,
        no_more: Callable[[bytearray, int, int], bool] = ...,
        buffer: None = ...,
        buffer_offset: int = ...,
        *,
        echo: bool = ...,
    ) -> bytes: ...

    @overload
    def read_raw(
        self,
        timeout: int | float | None = ...,
        minimum: int = ...,
        maximum: int | None = ...,
        no_more: Callable[[bytearray, int, int], bool] = ...,
        buffer: bytearray = ...,
        buffer_offset: int = ...,
        *,
        echo: bool = ...,
    ) -> int: ...

    def read_raw(
        self,
        timeout: int | float | None = None,
        minimum: int = 0,
        maximum: int | None = None,
        no_more: Callable[[bytearray, int, int], bool] = lambda *_: False,
        buffer: bytearray | None = None,
        buffer_offset: int = 0,
        *,
        echo: bool = False,
    ) -> bytes | int:
        """
        read_raw(\
            timeout = None,\
            minimum = 0,\
            maximum = None,\
            no_more = lambda *_: False,\
            buffer = None,\
            buffer_offset = 0,\
            *,\
            echo = False,\
        ) ->
        read_raw(..., buffer: None, ...) -> bytes
        read_raw(..., buffer: bytearray, ...,) -> int

        Reads from the device, with or without blocking.

        Args:
            timeout: Time limit for reading/awaiting bytes, in seconds.
            minimum: The **minimum** number of bytes to read.
            maximum: The **maximum** number of bytes to read (``None`` implies
              infinity). If *buffer* is not ``None``, and:

              - *maximum* is ``None``, the **available** size of the buffer is used
                instead;
              - otherwise, *maximum* must not be greater than the **available** size
                of the buffer.

            no_more: A callable which returns a boolean when passed:

              - the buffer into which bytes are being read (*buffer* itself, if not
                ``None``),
              - the index to which the first byte was written, and
              - the index to which the last byte was written.

              If it returns:

              - ``True``, no more bytes are read and the method returns immediately.
              - ``False``, more bytes are read.

              The default value always returns ``False``.

            buffer: A pre-allocated buffer to read into.
            buffer_offset: The index from which to start writing to *buffer*.
              Ignored if *buffer* is ``None``.
            echo: Whether or not input should be displayed on the screen, if the device
              is connected to a terminal emulator. Any input before or after the call
              is not affected.

        Returns:
            - The bytes read (empty, if *minimum* == ``0`` (default) and no bytes are
              read, or *maximum* == ``0``), if *buffer* is ``None``, OR
            - The number of bytes written to *buffer*, if *buffer* is not ``None``.

        Raises:
            ValueError: *minimum* or *maximum* is out of range.
            ValueError: *buffer* is empty or *buffer_offset* is out of range.

        The call blocks until *minimum* bytes have been read, regardless of *timeout*.
        Then, **up to** ``maximum - minimum`` additional bytes are read:

        - **without blocking** (i.e if readily available), if *timeout* is ``None``
          (default), OR
        - until a call to *no_more* returns ``True`` or *timeout* elapses,
          if *timeout* is not ``None``.

          .. note::
             - *timeout* elapses while reading *minimum* bytes, and additional
               bytes are read only if *timeout* hasn't elapsed.
             - If *timeout* < ``0``, it never elapses.
             - At this stage, bytes are read one at a time and *no_more* is called
               after each byte is read.

        Upon return or interruption, the device is **immediately** restored to the
        state in which it was met.

        IMPORTANT:
            If *buffer* is not ``None``, it cannot be resized until the call returns.
        """
        if minimum < 0:
            raise arg_value_error_msg("'minimum' is negative", minimum)

        if maximum is not None and minimum > maximum:
            raise arg_value_error_msg(
                "'minimum' is greater than 'maximum'",
                minimum,
                got_extra=f"{maximum=!r}",
            )

        if buffer is not None:
            # Dissalows resize of the buffer.
            # The view is automatically released upon return or interruption.
            buffer_memory = memoryview(buffer)

            if not buffer:
                raise ValueError("'buffer' is empty")

            if not 0 <= buffer_offset < len(buffer):
                raise arg_value_error_range(
                    "buffer_offset", buffer_offset, got_extra=f"{len(buffer)=!r}"
                )

            available_size = len(buffer) - buffer_offset
            if maximum is None:
                if minimum > available_size:
                    raise arg_value_error_msg(
                        "'minimum' is greater than the available size of 'buffer'",
                        minimum,
                        got_extra=f"{available_size=!r}",
                    )
                maximum = available_size
            elif maximum > available_size:
                raise arg_value_error_msg(
                    "'maximum' is greater than the available size of 'buffer'",
                    maximum,
                    got_extra=f"{available_size=!r}",
                )

        if maximum == 0:
            return 0 if buffer else b""

        if buffer:
            buffer_supplied = True
            buffer_index = buffer_offset
        else:
            buffer_supplied = False
            buffer_index = buffer_offset = 0
            buffer = bytearray()

        if maximum:
            buffer_end = buffer_offset + maximum

        tty_fd = self.fileno()
        old_attr = termios.tcgetattr(tty_fd)
        new_attr = termios.tcgetattr(tty_fd)

        new_attr[3] &= ~termios.ICANON  # Disable canonical mode
        if echo:
            new_attr[3] |= termios.ECHO  # Enable input echo
        else:
            new_attr[3] &= ~termios.ECHO  # Disable input echo
        new_attr[6][termios.VTIME] = 0  # Never block based on time
        # Block until *minimum* bytes have been read
        new_attr[6][termios.VMIN] = minimum

        try:
            w: list[int]
            x: list[int]
            r, w, x = [tty_fd], [], []
            termios.tcsetattr(tty_fd, termios.TCSANOW, new_attr)

            if timeout is None:
                if minimum:
                    bytes_read = os.read(tty_fd, maximum or 512)
                    buffer_index += (n_bytes := len(bytes_read))
                    buffer[buffer_index - n_bytes : buffer_index] = bytes_read

                    # Don't block based on based on amount of bytes any longer
                    new_attr[6][termios.VMIN] = 0
                    termios.tcsetattr(tty_fd, termios.TCSANOW, new_attr)

                maximum_remainder = buffer_end - buffer_index if maximum else 512
                while maximum_remainder and select(r, w, x, 0.0)[0]:
                    if bytes_read := os.read(tty_fd, maximum_remainder):
                        buffer_index += (n_bytes := len(bytes_read))
                        buffer[buffer_index - n_bytes : buffer_index] = bytes_read
                        if maximum:
                            maximum_remainder = buffer_end - buffer_index
            else:
                timeout = float(timeout)
                start = monotonic()

                if minimum:
                    bytes_read = os.read(tty_fd, minimum)
                    buffer_index += minimum
                    buffer[buffer_index - minimum : buffer_index] = bytes_read

                    # Don't block based on based on amount of bytes any longer
                    new_attr[6][termios.VMIN] = 0
                    termios.tcsetattr(tty_fd, termios.TCSANOW, new_attr)
                else:
                    bytes_read = b""

                if not (infinite := timeout < 0.0):
                    duration = monotonic() - start

                while (infinite or duration < timeout) and (
                    # If zero bytes were read in the previous iteration (for whatever
                    # reason), we are sure the maximum hasn't been reached and
                    # `no_more()` still returns `False`.
                    not bytes_read
                    or (
                        (not maximum or buffer_index < buffer_end)
                        and not no_more(buffer, buffer_offset, buffer_index - 1)
                    )
                ):
                    if (
                        select(r, w, x, None if infinite else timeout - duration)[0]
                        and (bytes_read := os.read(tty_fd, 1))  # fmt: skip
                    ):
                        buffer_index += 1
                        buffer[buffer_index - 1 : buffer_index] = bytes_read

                    if not infinite:
                        duration = monotonic() - start
        finally:
            if buffer_supplied:
                buffer_memory.release()
            termios.tcsetattr(tty_fd, termios.TCSANOW, old_attr)

        return buffer_index - buffer_offset if buffer_supplied else bytes(buffer)


# Non-decorator Functions
# ======================================================================================


def get_active_terminal() -> TTY:
    """Determines the :term:`active terminal`.

    Returns:
        The :py:class:`TTY` instance associated with the active terminal.

    Raises:
        NoActiveTerminalError: On an unsupported platform or the process does
          not seem to be connected to a TTY[-like] device.
    """
    global _tty, _tty_determined

    if _tty:
        return _tty

    if not OS_IS_UNIX:
        raise NoActiveTerminalError("Not supported on this platform")

    if _tty_determined:
        raise NoActiveTerminalError(
            "This process does not seem to be connected to a TTY[-like] device"
        )

    _tty_determined = True

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
        raise NoActiveTerminalError(
            "This process does not seem to be connected to a TTY[-like] device"
        ) from None

    return _tty


# Variables
# ======================================================================================


_tty: TTY | None = None
_tty_determined: bool = False
