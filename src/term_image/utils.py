"""
.. Utilities
"""

from __future__ import annotations

__all__ = (
    "get_cell_size",
    "get_terminal_name_version",
    "get_terminal_size",
    "lock_tty",
    "read_tty_all",
    "write_tty",
)

import os
import sys
import warnings
from array import array
from collections.abc import Callable
from functools import wraps
from multiprocessing import Array, Process, Queue as mp_Queue, RLock as mp_RLock
from operator import floordiv
from queue import Empty, Queue
from shutil import get_terminal_size as _get_terminal_size
from threading import RLock
from time import monotonic

from typing_extensions import (
    Any,
    Literal,
    ParamSpec,
    Tuple,
    TypeVar,
    no_type_check,
    overload,
)

import term_image

from . import _ctlseqs as ctlseqs
from .exceptions import TermImageUserWarning

# import logging

OS_IS_UNIX: bool
try:
    import fcntl
    import termios
    from select import select
except ImportError:
    OS_IS_UNIX = False
else:
    OS_IS_UNIX = True

# NOTE: Any cached feature using a query should have it's cache invalidated in
# `term_image.enable_queries()`.

# Type Variables and Aliases

P = ParamSpec("P")
T = TypeVar("T")

ColorType = Tuple[int, int, int]

# Variables

HEX_RGB_FMT = "#" + "%02x" * 3

# Decorator Classes


class ClassInstanceMethod(classmethod):  # type: ignore[type-arg]
    """A method which when invoked via the owner, behaves like a class method
    and when invoked via an instance, behaves like an instance method.
    """

    @no_type_check  # This definition is to be removed soon
    def __init__(self, f_owner, f_instance=None):
        super().__init__(f_owner)
        self.f_owner = f_owner
        self.f_instance = f_instance

    @no_type_check  # This definition is to be removed soon
    def __get__(self, instance, owner=None):
        if instance:
            return self.f_instance.__get__(instance, owner)
        else:
            return super().__get__(instance, owner)

    @no_type_check  # This definition is to be removed soon
    def classmethod(self, function):
        return type(self)(function, self.f_instance)

    @no_type_check  # This definition is to be removed soon
    def instancemethod(self, function):
        return type(self)(self.f_owner, function)


class ClassPropertyBase(property):
    """Base class for owner properties that also have a counterpart/shadow on the
    instance.
    """

    @no_type_check  # This definition is to be removed soon
    def __init__(self, fget=None, fset=None, fdel=None, doc=None):
        super().__init__(fget, fset, fdel, doc)
        # `property` doesn't set `__doc__`, probably cos the subclass' `__doc__`
        # attribute overrides its `__doc__` descriptor.
        super().__setattr__("__doc__", doc or fget.__doc__)


class ClassInstanceProperty(ClassPropertyBase):
    """A an instance-specific counterpart of a property of the owner.

    Operation on the owner is actually implemented by a property defined on the
    owner's metaclass. This class is only for the sake of ease of documentation
    without having to bother the user about metaclasses.
    """


class ClassProperty(ClassPropertyBase):
    """A read-only shadow of a property of the owner.

    Operation on the owner is actually implemented by a property defined on the
    owner's metaclass. This class is only for the sake of ease of documentation
    without having to bother the user about metaclasses.
    """


# Decorator Functions


def no_redecorate(decor: Callable[P, T]) -> Callable[P, T]:
    """Prevents a decorator from re-decorating objects.

    Args:
        decor: The decorator to be wrapped.

    Also used to mark decorators for auto documentation.
    """
    if hasattr(decor, "_no_redecorate_wrapped_"):
        return decor

    @wraps(decor)
    def no_redecorate_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        obj: Any = args[0]
        if not hasattr(obj, f"_{decor.__name__}_wrapped_"):
            obj = decor(*args, **kwargs)
            setattr(obj, f"_{decor.__name__}_wrapped_", ...)

        return obj  # type: ignore[no-any-return]

    setattr(no_redecorate_wrapper, "_no_redecorate_wrapped_", ...)

    return no_redecorate_wrapper


@no_redecorate
def cached(func: Callable[P, T]) -> Callable[P, T]:
    """Enables return value caching.

    Args:
        func: The function to be wrapped.

    An *_invalidate_cache* function is set as an attribute of the returned wrapper
    which when called clears the cache, so that the next call actually calls the
    wrapped function, no matter the value of *_cached*.

    NOTE:
        It's thread-safe, i.e there is no race condition between calls to the same
        decorated object across threads of the same process.

        Only works when function arguments, if any, are hashable.
    """

    @wraps(func)
    def cached_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        arguments = (args, tuple(kwargs.items()))
        with lock:
            try:
                return cache[arguments]
            except KeyError:
                return cache.setdefault(arguments, func(*args, **kwargs))

    def invalidate() -> None:
        with lock:
            cache.clear()

    cache: dict[tuple[P.args, tuple[tuple[str, Any], ...]], T] = {}
    lock = RLock()
    setattr(cached_wrapper, "_invalidate_cache", invalidate)

    return cached_wrapper


@no_redecorate
def lock_tty(func: Callable[P, T]) -> Callable[P, T]:
    """Synchronizes access to the :term:`active terminal`.

    Args:
        func: The function to be wrapped.

    When a decorated function is called, a re-entrant lock is acquired by the current
    process or thread and released after the call, such that any other decorated
    function called within another thread or subprocess waits until the lock is
    fully released (i.e has been released as many times as acquired) by the current
    process or thread.

    NOTE:
        It works across parent-/sub-processes, started directly or indirectly via
        :py:class:`multiprocessing.Process` (or a subclass of it), and their threads,
        provided :py:mod:`multiprocessing.synchronize` is supported on the host
        platform. Otherwise, a warning is issued when starting a subprocess.

    WARNING:
        If :py:mod:`multiprocessing.synchronize` is supported and a subprocess is
        started within a call (possibly recursive) to a decorated function, the thread
        in which that occurs will be out of sync until that call returns.
        Hence, avoid starting a subprocess within a decorated function.
    """

    @wraps(func)
    def lock_tty_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        # If a thread reaches this point while the lock is being changed
        # (the old lock has been acquired but hasn't been changed), after the lock has
        # been changed and the former lock is released, the waiting thread will acquire
        # the old lock making it to be out of sync.
        # Hence the second expression, which allows such a thread to acquire the new
        # lock and be in sync.
        # NB: Multiple expressions are processed as multiple nested with statements.
        with _tty_lock, _tty_lock:
            # logging.debug(f"{func.__name__} acquired TTY lock", stacklevel=3)
            return func(*args, **kwargs)

    if func.__module__.startswith("term_image") and func.__doc__ is not None:
        sync_doc = """

        IMPORTANT:
            Synchronized with :py:func:`~term_image.utils.lock_tty`.
        """

        last_line = func.__doc__.rpartition("\n")[2]
        indent = " " * (len(last_line) - len(last_line.lstrip()))
        lock_tty_wrapper.__doc__ = func.__doc__.rstrip() + "\n".join(
            line.replace(" " * 8, indent, 1) for line in sync_doc.splitlines()
        )

    return lock_tty_wrapper


@no_redecorate
def terminal_size_cached(func: Callable[P, T]) -> Callable[P, T]:
    """Enables return value caching based on the size of the :term:`active terminal`.

    Args:
        func: The function to be wrapped.

    If the terminal size is the same as for the last call, the last return value is
    returned. Otherwise the wrapped function is called and the new return value is
    stored and returned.

    An *_invalidate_terminal_size_cache* function is also set as an attribute of the
    returned wrapper which when called clears the cache, so that the next call actually
    calls the wrapped function.

    NOTE:
        It's thread-safe, i.e there is no race condition between calls to the same
        decorated callable across threads of the same process.
    """
    cache: tuple[T, os.terminal_size] | None = None
    lock = RLock()

    @wraps(func)
    def terminal_size_cached_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        nonlocal cache

        with lock:
            ts = get_terminal_size()
            if not cache or ts != cache[1]:
                cache = (func(*args, **kwargs), ts)

        return cache[0]

    def invalidate() -> None:
        nonlocal cache

        with lock:
            cache = None

    setattr(terminal_size_cached_wrapper, "_invalidate_terminal_size_cache", invalidate)

    return terminal_size_cached_wrapper


@no_redecorate
def unix_tty_only(func: Callable[P, T]) -> Callable[P, T | None]:
    """Disable invocation of a function on a non-unix-like platform or when there is no
    :term:`active terminal`.

    Args:
        func: The function to be wrapped.
    """

    @wraps(func)
    def unix_only_wrapper(*args: P.args, **kwargs: P.kwargs) -> T | None:
        return None if _tty_fd == -1 else func(*args, **kwargs)

    if unix_only_wrapper.__doc__ is None:
        unix_only_wrapper.__doc__ = ""

    unix_only_wrapper.__doc__ += """
    NOTE:
        Currently works on UNIX only, returns ``None`` on any other platform or when
        there is no :term:`active terminal`.
    """

    return unix_only_wrapper


# Non-decorators


def arg_type_error(arg: str, value: Any, got_extra: str = "") -> TypeError:
    return TypeError(
        f"Invalid type for {arg!r} (got: {type(value).__qualname__}; {got_extra})"
        if got_extra
        else f"Invalid type for {arg!r} (got: {type(value).__qualname__})"
    )


def arg_type_error_msg(msg: str, value: Any, got_extra: str = "") -> TypeError:
    return TypeError(
        f"{msg} (got: {type(value).__qualname__}; {got_extra})"
        if got_extra
        else f"{msg} (got: {type(value).__qualname__})"
    )


def arg_value_error(arg: str, value: Any, got_extra: str = "") -> ValueError:
    return ValueError(
        f"Invalid value for {arg!r} (got: {value!r}; {got_extra})"
        if got_extra
        else f"Invalid value for {arg!r} (got: {value!r})"
    )


def arg_value_error_msg(msg: str, value: Any, got_extra: str = "") -> ValueError:
    return ValueError(
        f"{msg} (got: {value!r}; {got_extra})"
        if got_extra
        else f"{msg} (got: {value!r})"
    )


def arg_value_error_range(arg: str, value: Any, got_extra: str = "") -> ValueError:
    return ValueError(
        f"{arg!r} out of range (got: {value!r}; {got_extra})"
        if got_extra
        else f"{arg!r} out of range (got: {value!r})"
    )


def clear_queue(queue: Queue[Any] | mp_Queue[Any]) -> None:
    """Purges the given queue"""
    while True:
        try:
            queue.get(timeout=0.005)
        except Empty:
            break


def color(
    text: str,
    fg: tuple[int, int, int] | None = None,
    bg: tuple[int, int, int] | None = None,
    *,
    end: bool = False,
) -> str:
    """Prepends *text* with direct-color escape sequences for the given foreground
    and/or background RGB values, optionally ending with the color reset sequence.

    Args:
        text: String to be color-coded.
        fg: Foreground color.
        bg: Background color.
        end: If ``True``, the color reset sequence is appended to the returned string.

    Returns:
        The color-coded string.

    The color code is omitted for any of *fg* or *bg* that is empty.
    """
    return (
        ctlseqs.SGR_FG_DIRECT * bool(fg) + ctlseqs.SGR_BG_DIRECT * bool(bg) + "%s"
    ) % (
        *(fg or ()),
        *(bg or ()),
        text,
    ) + ctlseqs.SGR_DEFAULT * end


@unix_tty_only
def get_cell_size() -> term_image.geometry.Size | None:
    """Returns the current size of a character cell in the :term:`active terminal`.

    Returns:
        The cell size in pixels or ``None`` if undetermined.

    The speed of this implementation is almost entirely dependent on the terminal; the
    method it supports and its response time if it has to be queried.
    """
    from term_image.geometry import _Size

    cell_size: tuple[int, ...]
    text_area_size: tuple[int, ...]
    cell_size = text_area_size = (0, 0)
    got_text_area_size = False

    # If a thread reaches this point while the lock is being changed
    # (the old lock has been acquired but hasn't been changed), after the lock has
    # been changed and the former lock is released, the waiting thread will acquire
    # the old lock making it to be out of sync.
    # Hence the second expression, which allows such a thread to acquire the new
    # lock and be in sync.
    # NB: Multiple expressions are processed as multiple nested with statements.
    with _cell_size_lock, _cell_size_lock:
        terminal_size = get_terminal_size()
        if terminal_size == tuple(_cell_size_cache[:2]):
            cell_size = tuple(_cell_size_cache[2:])
            return None if 0 in cell_size else _Size(*cell_size)

        # First try ioctl
        buf = array("H", [0, 0, 0, 0])
        try:
            if not fcntl.ioctl(_tty_fd, termios.TIOCGWINSZ, buf):
                text_area_size = tuple(buf[2:])
                if 0 not in text_area_size:
                    got_text_area_size = True
        except OSError:
            pass

        if not got_text_area_size:
            # Then XTWINOPS
            # The last sequence is to speed up the entire query since most (if not all)
            # terminals should support it and most terminals treat queries as FIFO
            response = query_terminal(
                ctlseqs.CELL_SIZE_PX_b + ctlseqs.TEXT_AREA_SIZE_PX_b + ctlseqs.DA1_b,
                more=lambda s: not s.endswith(b"c"),
            )
            if response:
                cell_size_match = ctlseqs.CELL_SIZE_PX_re.match(response.decode())
                if not cell_size_match:
                    text_area_size_match = ctlseqs.TEXT_AREA_SIZE_PX_re.match(
                        response.decode()
                    )
            # XTWINOPS specifies (height, width)
            if cell_size_match:
                cell_size = tuple(map(int, cell_size_match.groups()))[::-1]
            elif text_area_size_match:
                text_area_size = tuple(map(int, text_area_size_match.groups()))[::-1]

                # Termux seems to respond with (height / 2, width), though the values
                # are incorrect as they change with different zoom levels but still
                # always give a reasonable (almost always the same) cell size and ratio.
                if os.environ.get("SHELL", "").startswith("/data/data/com.termux/"):
                    text_area_size = (text_area_size[0], text_area_size[1] * 2)

        if got_text_area_size:
            if _swap_win_size:
                text_area_size = text_area_size[::-1]
            cell_size = tuple(map(floordiv, text_area_size, terminal_size))

        _cell_size_cache[:] = terminal_size + cell_size

        return None if 0 in cell_size else _Size(*cell_size)


@overload
def get_fg_bg_colors(
    *, hex: Literal[False]
) -> tuple[ColorType | None, ColorType | None]: ...


@overload
def get_fg_bg_colors(*, hex: Literal[True]) -> tuple[str | None, str | None]: ...


@cached
def get_fg_bg_colors(
    *, hex: Literal[False, True] = False
) -> tuple[ColorType | str | None, ColorType | str | None]:
    """
    get_fg_bg_colors(*, hex: Literal[False]) \
    -> tuple[ColorType | None, ColorType | None]
    get_fg_bg_colors(*, hex: Literal[True]) -> tuple[str | None, str | None]

    Returns the default FG and BG colors of the :term:`active terminal`.

    Returns:
        For each color,

        * an RGB 3-tuple, if *hex* is ``False``
        * an RGB hex string if *hex* is ``True``
        * ``None`` if undetermined
    """
    # The terminal's response to the queries is not read all at once
    with _tty_lock, _tty_lock:  # See the comment in `lock_tty_wrapper()`
        response = query_terminal(
            # Not all terminals (e.g VTE-based) support multiple queries in one escape
            # sequence, hence the separate sequences for FG and BG
            ctlseqs.TEXT_FG_QUERY_b + ctlseqs.TEXT_BG_QUERY_b + ctlseqs.DA1_b,
            # The response might contain a "c"; can't stop reading at "c"
            lambda s: not s.endswith(ctlseqs.CSI_b),
        )
        if _queries_enabled:
            read_tty()  # The rest of the response to DA1

    fg = bg = None
    if response:
        for c, spec in ctlseqs.RGB_SPEC_re.findall(response.decode()):
            if c == "10":
                fg = ctlseqs.x_parse_color(spec)
            elif c == "11":
                bg = ctlseqs.x_parse_color(spec)

    return (
        fg and (HEX_RGB_FMT % fg if hex else fg),
        bg and (HEX_RGB_FMT % bg if hex else bg),
    )


@cached
def get_terminal_name_version() -> tuple[str | None, str | None]:
    """Returns the name and version of the :term:`active terminal`, if available.

    Returns:
        A 2-tuple, ``(name, version)``. If either is not available, returns ``None``
        in its place.
    """
    # The terminal's response to the queries is not read all at once
    with _tty_lock, _tty_lock:  # See the comment in `lock_tty_wrapper()`
        # Terminal name/version query + terminal attribute query
        # The latter is to speed up the entire query since most (if not all)
        # terminals should support it and most terminals treat queries as FIFO
        response = query_terminal(
            ctlseqs.XTVERSION_b + ctlseqs.DA1_b,
            # The response might contain a "c"; can't stop reading at "c"
            lambda s: not s.endswith(ctlseqs.CSI_b),
        )
        if _queries_enabled:
            read_tty()  # The rest of the response to DA1

    match = response and ctlseqs.XTVERSION_re.match(response.decode())
    name, version = (
        match.groups()
        if match
        else map(os.environ.get, ("TERM_PROGRAM", "TERM_PROGRAM_VERSION"))
    )

    return (name and name.lower(), version)


def get_terminal_size() -> os.terminal_size:
    """Returns the current size of the :term:`active terminal`.

    Returns:
        The terminal size in columns and lines.

    NOTE:
        This implementation is quite different from :py:func:`shutil.get_terminal_size`
        and :py:func:`os.get_terminal_size` in that it:

        - gives the correct size of the :term:`active terminal` even when output is
          redirected, in most cases
        - gives different results in certain situations
        - is what this library works with
    """
    size = None
    if _tty_fd != -1:
        # faster and gives correct results when output is redirected
        try:
            size = os.get_terminal_size(_tty_fd)
        except OSError:
            pass

    return size or _get_terminal_size()


@unix_tty_only
@lock_tty
def query_terminal(
    request: bytes, more: Callable[[bytearray], bool], timeout: float | None = None
) -> bytes | None:
    """Sends a query to the :term:`active terminal` and returns the response.

    Args:
        more: A callable, which when passed the response received so far, returns a
          boolean indicating if the response is incomplete or not. If it returns:

          * ``True``, more response is waited for.
          * ``False``, the received response is returned immediately.

        timeout: Time limit for awaiting a response from the terminal, in seconds
          (infinite if negative).

          If not given or ``None``, the value set by
          :py:func:`~term_image.set_query_timeout`
          (or :py:data:`~term_image.DEFAULT_QUERY_TIMEOUT` if never set) is used.

    Returns:
        `None` if queries are disabled (via :py:func:`~term_image.disable_queries`),
        else the terminal's response (empty, if no response is received after
        *timeout* is up).

    ATTENTION:
        Any unread input is discarded before the query. If the input might be needed,
        it can be read using :py:func:`read_tty()` before calling this function.
    """
    if not _queries_enabled:
        return None

    old_attr = termios.tcgetattr(_tty_fd)
    new_attr = termios.tcgetattr(_tty_fd)
    new_attr[3] &= ~termios.ECHO  # Disable input echo
    try:
        termios.tcsetattr(_tty_fd, termios.TCSAFLUSH, new_attr)
        write_tty(request)
        return read_tty(more, timeout or _query_timeout)
    finally:
        termios.tcsetattr(_tty_fd, termios.TCSANOW, old_attr)


@unix_tty_only
@lock_tty
def read_tty(
    more: Callable[[bytearray], bool] = lambda _: True,
    timeout: float | None = None,
    min: int = 0,
    *,
    echo: bool = False,
) -> bytes | None:
    """Reads input directly from the :term:`active terminal` with/without blocking.

    Args:
        more: A callable, which when passed the input received so far, as a `bytearray`
          object, returns a boolean. If it returns:

          * ``True``, more input is waited for.
          * ``False``, the input received so far is returned immediately.

        timeout: Time limit for awaiting input, in seconds.
        min: Causes to block until at least the given number of bytes have been read.
        echo: If ``True``, any input while waiting is printed unto the screen.
          Any input before or after calling this function is not affected.

    Returns:
        The input read (empty, if *min* == ``0`` (default) and no input is received
        before *timeout* is up) or ``None`` if not supported.

    If *timeout* is ``None`` (default), all available input is read without blocking.

    If *timeout* is not ``None`` and:

      * *timeout* < ``0``, it's infinite.
      * *min* > ``0``, input is waited for until at least *min* bytes have been read.

        After *min* bytes have been read, the following points apply with *timeout*
        being the leftover of the original *timeout*, if not yet used up.

      * *more* is not given, input is read or waited for until *timeout* is up.
      * *more* is given, input is read or waited for until ``more(input)`` returns
        ``False`` or *timeout* is up.

    Upon return or interruption, the :term:`active terminal` is **immediately** restored
    to the state in which it was met.
    """
    old_attr = termios.tcgetattr(_tty_fd)
    new_attr = termios.tcgetattr(_tty_fd)
    new_attr[3] &= ~termios.ICANON  # Disable canonical mode
    new_attr[6][termios.VTIME] = 0  # Never block based on time
    if echo:
        new_attr[3] |= termios.ECHO  # Enable input echo
    else:
        new_attr[3] &= ~termios.ECHO  # Disable input echo
    # Block until *min* bytes are read, when *timeout* is not `None`.
    new_attr[6][termios.VMIN] = 0 if timeout is None else min

    input = bytearray()
    try:
        w: list[int]
        x: list[int]
        r, w, x = [_tty_fd], [], []
        termios.tcsetattr(_tty_fd, termios.TCSANOW, new_attr)

        if timeout is None:
            # VMIN=0 does not work as expected on some platforms when there's no input
            while select(r, w, x, 0.0)[0]:
                input.extend(os.read(_tty_fd, 100))
        else:
            start = monotonic()
            if min > 0:
                input.extend(os.read(_tty_fd, min))

                # Don't block based on based on amount of bytes anymore
                new_attr[6][termios.VMIN] = 0
                termios.tcsetattr(_tty_fd, termios.TCSANOW, new_attr)

            duration = monotonic() - start
            while (timeout < 0 or duration < timeout) and more(input):
                # Reduces CPU usage
                # Also, VMIN=0 does not work on some platforms when there's no input
                if select(r, w, x, None if timeout < 0 else timeout - duration)[0]:
                    input.extend(os.read(_tty_fd, 1))
                duration = monotonic() - start
            # logging.debug(duration)
    finally:
        termios.tcsetattr(_tty_fd, termios.TCSANOW, old_attr)

    return bytes(input)


@unix_tty_only
def read_tty_all() -> bytes | None:
    """Reads all available input directly from the :term:`active terminal` **without
    blocking**.

    Returns:
        The input read or ``None`` if not supported.

    IMPORTANT:
        Synchronized with :py:func:`~term_image.utils.lock_tty`.
    """
    return read_tty()


@unix_tty_only
@lock_tty
def write_tty(data: bytes) -> None:
    """Writes to the :term:`active terminal` and waits until complete transmission.

    Args:
        data: Data to be written.
    """
    os.write(_tty_fd, data)
    try:
        termios.tcdrain(_tty_fd)
    except termios.error:  # "Permission denied" on some platforms e.g Termux
        pass


@no_type_check
def _process_start_wrapper(self, *args, **kwargs):
    global _tty_lock, _cell_size_cache, _cell_size_lock

    # Ensure a lock is not acquired by another process/thread before changing it.
    # The only case in which this is useless is when the owner thread is the
    # one starting a process. In such a situation, the owner thread will be partially
    # (may acquire the new lock in a nested call while still holding the old lock)
    # out of sync until it has fully released the old lock.

    with _tty_lock:
        if isinstance(_tty_lock, _rlock_type):
            try:
                self._tty_lock = _tty_lock = mp_RLock()
            except ImportError:
                self._tty_lock = None
                warnings.warn(
                    "Multi-process synchronization is not supported on this platform!\n"
                    "Hence, if any subprocess will be writing/reading to/from the "
                    "active terminal, it may be unsafe to use any features requiring"
                    "terminal queries.\n"
                    "See https://term-image.readthedocs.io/en/stable/guide/concepts"
                    ".html#terminal-queries\n"
                    "If any related issues occur, it's advisable to disable queries "
                    "using `term_image.disable_queries()`.\n"
                    "Simply set an 'ignore' filter for this warning (before starting "
                    "any subprocess) if not using any of the affected features.",
                    TermImageUserWarning,
                )
        else:
            self._tty_lock = _tty_lock

    with _cell_size_lock:
        if isinstance(_cell_size_lock, _rlock_type):
            try:
                self._cell_size_cache = _cell_size_cache = Array("i", _cell_size_cache)
                _cell_size_lock = _cell_size_cache.get_lock()
            except ImportError:
                self._cell_size_cache = None
        else:
            self._cell_size_cache = _cell_size_cache

    return _process_start_wrapper.__wrapped__(self, *args, **kwargs)


@no_type_check
def _process_run_wrapper(self, *args, **kwargs):
    global _tty_lock, _cell_size_cache, _cell_size_lock

    if self._tty_lock:
        _tty_lock = self._tty_lock
    if self._cell_size_cache:
        _cell_size_cache = self._cell_size_cache
        _cell_size_lock = _cell_size_cache.get_lock()

    return _process_run_wrapper.__wrapped__(self, *args, **kwargs)


# Private internal variables
_query_timeout = 0.1
_queries_enabled = True
_swap_win_size = False
_tty_fd = -1
_tty_lock = RLock()
_cell_size_cache = [0] * 4
_cell_size_lock = RLock()
_rlock_type = type(_tty_lock)

if OS_IS_UNIX:
    for stream in ("out", "in", "err"):  # In order of priority
        try:
            # A new file descriptor is required because, at least, both read and write
            # access to the T/PTY are required.
            _tty_fd = os.open(
                os.ttyname(getattr(sys, f"__std{stream}__").fileno()), os.O_RDWR
            )
            break
        except (OSError, AttributeError):
            pass
    else:
        try:
            _tty_fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError:
            warnings.warn(
                "It seems this process is not running within a terminal. "
                "Hence, some features will behave differently or be disabled.\n"
                "See https://term-image.readthedocs.io/en/stable/guide/concepts"
                ".html#active-terminal\n"
                "Any filter for this warning must be set before loading `term_image`, "
                "using `UserWarning` with the warning message (since "
                "`TermImageUserWarning` won't be available).",
                TermImageUserWarning,
            )

    if _tty_fd != -1:
        Process.start = wraps(Process.start)(  # type: ignore[method-assign]
            _process_start_wrapper
        )
        Process.run = wraps(Process.run)(  # type: ignore[method-assign]
            _process_run_wrapper
        )

        # Shouldn't be needed since we're getting our own separate file descriptors
        # but the validity of the assumed safety is still under probation
        """
        for name, value in vars(termios).items():
            if isinstance(value, BuiltinFunctionType):
                setattr(termios, name, lock_tty(value))
        """
