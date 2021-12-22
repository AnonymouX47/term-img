"""Term-Img's Terminal User Interface"""

from __future__ import annotations

import argparse
import logging
from typing import Iterable, Iterator, Tuple, Union

import urwid

from .main import MainLoop, palette, _process_input
from .widgets import Image, info_bar, main as main_widget
from . import main
from .. import cli


def init(
    args: argparse.Namespace,
    images: Iterable[Tuple[str, Union[Image, Iterator]]],
    contents: dict,
) -> None:
    """Initializes the TUI"""
    from ..logging import log

    global launched

    if cli.args.debug:
        main_widget.contents.insert(
            -1, (urwid.AttrMap(urwid.Filler(info_bar), "input"), ("given", 1))
        )

    loop = MainLoop(main_widget, palette, unhandled_input=_process_input)
    loop.screen.clear()
    loop.screen.set_terminal_properties(2 ** 24)

    main.loop = loop
    main.max_pixels = args.max_pixels
    main.recursive = args.recursive
    main.show_hidden = args.all
    main.displayer = main.display_images(".", iter(images), contents, top_level=True)

    logger = logging.getLogger(__name__)
    log("Launching TUI", logger, direct=False)
    launched = True

    next(main.displayer)
    try:
        main.loop.run()
        log("Exited TUI normally", logger, direct=False)
    finally:
        launched = False


launched = False
