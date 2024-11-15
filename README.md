## Term-Image

Display images in the terminal with Python.

<p align="center">
<img src="https://raw.githubusercontent.com/AnonymouX47/term-image/92ff4b2d2e4731be9e1b2ac7378964ebed9f10f9/docs/source/resources/logo.png" height="200">
</p>

**Docs:** [Read the Docs](https://term-image.readthedocs.io)  
**Tutorial:** [Tutorial](https://term-image.readthedocs.io/en/stable/start/tutorial.html)  

![PyPI Version](https://img.shields.io/pypi/v/term-image.svg)
![Monthly Downloads](https://pepy.tech/badge/term-image/month)
![Python Versions](https://img.shields.io/pypi/pyversions/term-image.svg)
![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)
![Last Commit](https://img.shields.io/github/last-commit/AnonymouX47/term-image)

---


## Features

- Supports multiple image formats (compatible with `PIL.Image.open()`).
- Various render styles for terminal images.
- Support for Kitty and iTerm2 graphics protocols.
- Transparency support.
- Animated image handling.
- Integration with terminal-based libraries.
- Automatic/manual resizing, alignment, and aspect ratio preservation.
- And more!

## Prerequisites

This project requires the following:

- Python >= 3.8
- Supported terminal emulators:
  - Kitty graphics protocol
  - iTerm2 inline image protocol
  - Terminals with full Unicode and truecolor support

Plans to support additional terminal emulators are in progress. See [Planned Features](#planned-features).

## Installation Steps

### Option 1: Install Stable Version
```shell
pip install term-image
```

### Option 2: Install Development Version
```shell
pip install git+https://github.com/AnonymouX47/term-image.git
```

### Verification
To verify the installation, import the library and try displaying an image (see Quick Start).

### Supported Terminal Emulators
See [here](https://term-image.readthedocs.io/en/stable/start/installation.html#supported-terminal-emulators).


## Demo

Check out [termvisage](https://github.com/AnonymouX47/termvisage), an image viewer based on this library.

## Quick Start

1. Create an image instance using file, URL, or `PIL` image:
   ```python
   from term_image.image import from_file
   image = from_file("path/to/image.png")
   ```
2. Display the image using:
   ```python
   image.draw()
   ```

For animated images, only `draw()` supports animations.

## Contribution

Refer to the [Contribution Guidelines](https://github.com/AnonymouX47/term-image/blob/main/CONTRIBUTING.md).

## Planned Features

- Additional terminal emulator support.
- Enhanced transparency features.

See [Milestones](https://github.com/AnonymouX47/term-image/milestones) for details.

## Known Issues

Known issues are documented [here](https://term-image.readthedocs.io/en/stable/issues.html).

## FAQs

See the [FAQs](https://term-image.readthedocs.io/en/stable/faqs.html) section of the documentation.

## Credits

This project uses:

- [Pillow](https://python-pillow.org)
- [Requests](https://requests.readthedocs.io)

Logo credits: [Flaticon](https://www.flaticon.com/free-icons/gallery).

## Sponsor This Project

Support this project by [Buying Me A Coffee](https://www.buymeacoffee.com/anonymoux47).

---

Thank you for using Term-Image!
