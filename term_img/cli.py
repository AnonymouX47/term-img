"""term-img's CLI Implementation"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import PIL
import requests

from .exceptions import InvalidSize, URLNotFoundError
from .exit_codes import FAILURE, INVALID_SIZE, NO_VALID_SOURCE, SUCCESS
from .image import _ALPHA_THRESHOLD, TermImage
from . import set_font_ratio, __version__


def check_dir(dir: str, prev_dir: str = "..") -> Optional[dict]:
    """Scan _dir_ (and sub-directories, if '--recursive' is set)
    and build the tree of directories [recursively] containing readable images.

    Args:
        - dir: Path to directory to be scanned.
        - prev_dir: Path to set as working directory after scannning _dir_
            (default:  parent directory of _dir_).

    Returns:
        - `None` if _dir_ contains no readable images [recursively].
        - A dict representing the resulting directory tree, if _dir_ is "non-empty".

    - If '--hidden' is set, hidden (.*) images and subdirectories are considered.
    """
    try:
        os.chdir(dir)
    except OSError:
        _log_exception(
            f"Could not access '{os.path.abspath(dir)}/'", logger, direct=True
        )
        return

    # Some directories can be changed to but cannot be listed
    try:
        entries = os.listdir()
    except OSError:
        _log_exception(
            f"Could not get the contents of '{os.path.abspath('.')}/'",
            logger,
            direct=True,
        )
        return os.chdir(prev_dir)

    empty = True
    content = {}
    for entry in entries:
        if entry.startswith(".") and not _SHOW_HIDDEN:
            continue
        if os.path.isfile(entry):
            if not empty:
                continue
            try:
                PIL.Image.open(entry)
                if empty:
                    empty = False
            except Exception:
                pass
        elif _RECURSIVE:
            try:
                if os.path.islink(entry):
                    # Eliminate broken and cyclic symlinks
                    # Return to the link's parent rather than the linked directory's
                    # parent
                    result = (
                        check_dir(entry, os.getcwd())
                        if (
                            os.path.exists(entry)
                            and not os.getcwd().startswith(os.path.realpath(entry))
                        )
                        else None
                    )
                else:
                    # The check is only to filter inaccessible files and disallow them
                    # from being reported as directories within the recursive call
                    result = check_dir(entry) if os.path.isdir(entry) else None
            except RecursionError:
                _log(f"Too deep: {os.getcwd()!r}", logger, logging.ERROR)
                # Don't bother checking anything else in the current directory
                # Could possibly mark the directory as empty even though it contains
                # image files but at the same time, could be very costly when
                # there are many subdirectories
                break
            if result is not None:
                content[entry] = result

    os.chdir(prev_dir)
    return None if empty and not content else content


def main():
    """CLI execution entry-point"""
    global args, _log, _log_exception, _RECURSIVE, _SHOW_HIDDEN

    # Ensure user-config is loaded only when the package is executed as a module,
    # from the CLI
    from .tui.config import (
        config_options,
        font_ratio,
        frame_duration,
        max_pixels,
        user_dir,
    )
    from .tui.main import scan_dir
    from .tui.widgets import Image
    from .logging import init_log, log, log_exception
    from . import notify
    from . import tui

    _log, _log_exception = log, log_exception

    parser = argparse.ArgumentParser(
        prog="term-img",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Display/Browse images in a terminal",
        epilog=""" \

'--' should be used to separate positional arguments that begin with an '-' \
from options/flags, to avoid ambiguity.
For example, `$ term-img [options] -- -image.jpg --image.png`

NOTES:
  1. The displayed image uses HEIGHT/2 lines and WIDTH columns.
  2. The size is multiplied by the scale on each axis respectively before the image
     is rendered. A scale value must be such that 0.0 < value <= 1.0.
  3. Any image having more pixels than the specified maximum will be replaced
     with a placeholder when displayed but can still be forced to display
     or viewed externally.
     Note that increasing this will have adverse effects on performance.
  4. Any event with a level lower than the specified one is not reported.
  5. Supports all image formats supported by `PIL.Image.open()`.
""",
        add_help=False,  # '-h' is used for HEIGHT
        allow_abbrev=False,  # Allow clustering of short options in 3.7
    )

    # General
    general = parser.add_argument_group("General Options")

    general.add_argument(
        "--help",
        action="help",
        help="Show this help message and exit",
    )
    general.add_argument(
        "--version",
        action="version",
        version=__version__,
        help="Show the program version and exit",
    )
    general.add_argument(
        "-f",
        "--font-ratio",
        type=float,
        metavar="N",
        default=font_ratio,
        help=(
            "Specify your terminal's font ratio for proper image scaling "
            f"(default: {font_ratio})"
        ),
    )
    general.add_argument(
        "-F",
        "--frame-duration",
        type=float,
        metavar="N",
        default=frame_duration,
        help=(
            "Specify the time (in seconds) between frames of an animated image "
            f"(default: {frame_duration})"
        ),
    )

    _alpha_options = parser.add_argument_group(
        "Transparency Options (General)",
        "NOTE: These are mutually exclusive",
    )
    alpha_options = _alpha_options.add_mutually_exclusive_group()
    alpha_options.add_argument(
        "--no-alpha",
        action="store_true",
        help="Disable image transparency (i.e black background)",
    )
    alpha_options.add_argument(
        "-A",
        "--alpha",
        type=float,
        metavar="N",
        default=_ALPHA_THRESHOLD,
        help=(
            "Alpha ratio above which pixels are taken as opaque (0 <= x < 1) "
            f"(default: {_ALPHA_THRESHOLD:f})"
        ),
    )
    alpha_options.add_argument(
        "-b",
        "--alpha-bg",
        metavar="COLOR",
        help=(
            "Hex color (without '#') with which transparent backgrounds should be "
            "replaced"
        ),
    )

    # CLI-only
    cli_options = parser.add_argument_group(
        "CLI-only Options",
        "These options apply only when there is just one valid image source",
    )

    size_options = cli_options.add_mutually_exclusive_group()
    size_options.add_argument(
        "-w",
        "--width",
        type=int,
        metavar="N",
        help="Width of the image to be rendered (default: auto) [1]",
    )
    size_options.add_argument(
        "-h",
        "--height",
        type=int,
        metavar="N",
        help="Height of the image to be rendered (default: auto) [1]",
    )
    cli_options.add_argument(
        "-x",
        "--scale-x",
        type=float,
        metavar="N",
        default=1.0,
        help="x-axis scale of the image to be rendered (default: 1.0) [2]",
    )
    cli_options.add_argument(
        "-y",
        "--scale-y",
        type=float,
        metavar="N",
        default=1.0,
        help="y-axis scale of the image to be rendered (default: 1.0) [2]",
    )

    align_options = parser.add_argument_group("Alignment Options (CLI-only)")
    align_options.add_argument(
        "--no-align",
        action="store_true",
        help=(
            "Output image without alignment or padding. "
            "Overrides all other alignment options"
        ),
    )
    align_options.add_argument(
        "-H",
        "--h-align",
        choices=("left", "center", "right"),
        help="Horizontal alignment (default: center)",
    )
    align_options.add_argument(
        "--pad-width",
        metavar="N",
        type=int,
        help="No of columns within which to align the image (default: terminal width)",
    )
    align_options.add_argument(
        "-V",
        "--v-align",
        choices=("top", "middle", "bottom"),
        help="Vertical alignment (default: middle)",
    )
    align_options.add_argument(
        "--pad-height",
        metavar="N",
        type=int,
        help=(
            "No of lines within which to align the image "
            "(default: terminal height, with a 2-line allowance)"
        ),
    )

    # TUI-only
    tui_options = parser.add_argument_group(
        "TUI-only Options",
        """These options apply only when there is at least one valid directory source \
or multiple valid sources
""",
    )

    tui_options.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Inlcude hidden file and directories",
    )
    tui_options.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Scan for local images recursively",
    )
    tui_options.add_argument(
        "--max-pixels",
        type=int,
        metavar="N",
        default=max_pixels,
        help=(
            "Maximum amount of pixels in images to be displayed "
            f"(default: {max_pixels}) [3]"
        ),
    )

    # Logging
    log_options_ = parser.add_argument_group(
        "Logging Options",
        "NOTE: These are mutually exclusive",
    )
    log_options = log_options_.add_mutually_exclusive_group()

    log_file = os.path.join(user_dir, "term_img.log")
    log_options_.add_argument(
        "-l",
        "--log",
        metavar="FILE",
        default=log_file,
        help=f"Specify a file to write logs to (default: {log_file})",
    )
    log_options.add_argument(
        "--log-level",
        metavar="LEVEL",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default="WARNING",
        help=(
            "Set logging level to any of DEBUG, INFO, WARNING, ERROR, CRITICAL "
            "(default: WARNING) [4]"
        ),
    )
    log_options.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="More detailed event reporting. Also implies --log-level=INFO",
    )
    log_options.add_argument(
        "--verbose-log",
        action="store_true",
        help="Like --verbose but only applies to the log file",
    )
    log_options.add_argument(
        "--debug",
        action="store_true",
        help="Implies --log-level=DEBUG with verbosity",
    )

    # Positional
    parser.add_argument(
        "sources",
        nargs="+",
        metavar="source",
        help="Path(s) to local image(s) and/or directory(s) OR URLs",
    )

    args = parser.parse_args()
    _RECURSIVE = args.recursive
    _SHOW_HIDDEN = args.all

    init_log(
        args.log,
        getattr(logging, args.log_level),
        args.debug,
        args.verbose,
        args.verbose_log,
    )

    for name, is_valid in config_options.items():
        var_name = name.replace(" ", "_")
        value = getattr(args, var_name, None)
        # Not all config options have corresponding command-line arguments
        if value is not None and not is_valid(value):
            notify.notify(
                f"Invalid {name} (got: {value})... Using config value.",
                level=notify.ERROR,
            )
            setattr(args, var_name, locals()[var_name])

    set_font_ratio(args.font_ratio)

    images = []
    contents = {}

    for source in args.sources:
        if all(urlparse(source)[:3]):  # Is valid URL
            log(
                f"Getting image from {source!r}...",
                logger,
                verbose=True,
            )
            try:
                images.append(
                    (os.path.basename(source), Image(TermImage.from_url(source))),
                )
            # Also handles `ConnectionTimeout`
            except requests.exceptions.ConnectionError:
                log(f"Unable to get {source!r}", logger, logging.ERROR)
            except URLNotFoundError as e:
                log(str(e), logger, logging.ERROR)
            except PIL.UnidentifiedImageError as e:
                log(str(e), logger, logging.ERROR)
            else:
                log("... Done!", logger, verbose=True)
        elif os.path.isfile(source):
            try:
                images.append(
                    (source, Image(TermImage.from_file(os.path.relpath(source))))
                )
            except PIL.UnidentifiedImageError as e:
                log(str(e), logger, logging.ERROR)
            except OSError as e:
                log(
                    f"({e}) {source!r} could not be read",
                    logger,
                    logging.ERROR,
                )
        elif os.path.isdir(source):
            log(
                f"Checking directory {source!r}...",
                logger,
                verbose=True,
            )
            result = check_dir(source, os.getcwd())
            log("... Done!", logger, verbose=True)
            if result is not None:
                source = os.path.abspath(source)
                contents[source] = result
                images.append((source, scan_dir(source, result, os.getcwd())))
        else:
            log(
                f"{source!r} is invalid or does not exist",
                logger,
                logging.ERROR,
            )

    if not images:
        log("No valid source!", logger)
        return NO_VALID_SOURCE

    if len(images) == 1 and isinstance(images[0][1], Image):
        log(
            "Single image source; Printing directly to console",
            logger,
            direct=False,
        )
        image = images[0][1]._image
        try:
            if args.width is not None:
                image.width = args.width
            elif args.height is not None:
                image.height = args.height
            image.scale_x = args.scale_x
            image.scale_y = args.scale_y
            image.frame_duration = args.frame_duration

            image.draw_image(
                *(
                    (None, 1, None, 1)
                    if args.no_align
                    else (args.h_align, args.pad_width, args.v_align, args.pad_height)
                ),
                (
                    None
                    if args.no_alpha
                    else args.alpha_bg and "#" + args.alpha_bg or args.alpha
                ),
            )

        # Handles `ValueError` and `.exceptions.InvalidSize`
        # raised by `TermImage.__valid_size()`, scaling value checks
        # or padding width/height checks.
        except ValueError as e:
            log(str(e), logger, logging.CRITICAL)
            return INVALID_SIZE if isinstance(e, InvalidSize) else FAILURE
    else:
        tui.init(args, images, contents)

    return SUCCESS


logger = logging.getLogger(__name__)

# Set from within `main()`
_RECURSIVE = None
_SHOW_HIDDEN = None
args = None  # Imported from within other modules
_log = None
_log_exception = None
