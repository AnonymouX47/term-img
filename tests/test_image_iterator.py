from types import GeneratorType

import pytest
from PIL import Image

from term_image.image import ImageIterator, TermImage

_size = (40, 20)

png_image = TermImage(Image.open("tests/images/python.png"))
gif_img = Image.open("tests/images/lion.gif")
gif_image = TermImage(gif_img)
webp_img = Image.open("tests/images/anim.webp")
webp_image = TermImage(webp_img)

gif_image._size = _size
webp_image._size = _size


def test_arg_checks():
    for value in ("tests/images/anim.webp", gif_img, webp_img):
        with pytest.raises(TypeError, match="'image'"):
            ImageIterator(value)
    with pytest.raises(ValueError, match="not animated"):
        ImageIterator(png_image)

    for value in (None, 2.0, 0.2, "2"):
        with pytest.raises(TypeError, match="'repeat'"):
            ImageIterator(gif_image, value)
    with pytest.raises(ValueError, match="'repeat'"):
        ImageIterator(gif_image, 0)

    for value in (None, 2.0, 2):
        with pytest.raises(TypeError, match="'format'"):
            ImageIterator(gif_image, format=value)
    with pytest.raises(ValueError, match="format specification"):
        ImageIterator(gif_image, format=".")

    for value in (None, 2.0, "2"):
        with pytest.raises(TypeError, match="'cached'"):
            ImageIterator(gif_image, cached=value)
    for value in (0, -1, -10):
        with pytest.raises(ValueError, match="'cached'"):
            ImageIterator(gif_image, cached=value)


def test_init():
    def test_defaults(image):
        image_it = ImageIterator(image)
        assert image_it._image is image
        assert image_it._repeat == -1
        assert image_it._format == ""
        assert image_it._cached is (image.n_frames <= 100)
        assert isinstance(image_it._animator, GeneratorType)

    def test_with_args(image, repeat, format, cached):
        image_it = ImageIterator(image, repeat, format, cached)
        assert image_it._image is image
        assert image_it._repeat == repeat
        assert image_it._format == format
        assert image_it._cached is (
            cached if isinstance(cached, bool) else image.n_frames <= cached
        )
        assert isinstance(image_it._animator, GeneratorType)

    for image in (gif_image, webp_image):
        test_defaults(image)

    for args in (
        (-1, "", 100),
        (2, "#", True),
        (10, "1.1", False),
        (100, "#.9", 1),
    ):
        test_with_args(gif_image, *args)

    # caching is disabled if repeat == 1
    for value in (True, 1, 100):
        image_it = ImageIterator(gif_image, 1, cached=value)
        assert image_it._cached is False


def test_next():
    image_it = ImageIterator(gif_image, 1, "1.1")
    assert isinstance(next(image_it), str)

    for _ in range(gif_image.n_frames - 1):
        next(image_it)

    with pytest.raises(StopIteration):
        next(image_it)

    # Frame number is set to zero
    assert gif_image.tell() == 0

    # Iterator is closed
    assert not hasattr(image_it, "_animator")
    assert not hasattr(image_it, "_img")

    # All calls after StopIteration is first raised also raise StopIteration
    for _ in range(2):
        with pytest.raises(StopIteration):
            next(image_it)


def test_iter():
    image_it = ImageIterator(gif_image, 1, "1.1")
    assert iter(image_it) is image_it

    for image in (gif_image, webp_image):
        frames = tuple(ImageIterator(image, 1, "1.1"))
        assert len(frames) == image.n_frames
        assert all(isinstance(frame, str) for frame in frames)

        # Consecutive frames are different
        prev_frame = None
        for frame in frames:
            assert frame != prev_frame
            prev_frame = frame

    # Frames are the same as for manual iteration
    gif_image2 = TermImage.from_file(gif_image._source.filename)
    gif_image2._size = _size
    for n, frame in enumerate(ImageIterator(gif_image, 1, "1.1")):
        gif_image2.seek(n)
        assert frame == str(gif_image2)


def test_repeat():
    for image in (gif_image, webp_image):
        for value in (False, True):
            frames = tuple(ImageIterator(image, 2, "1.1", cached=value))

            # # Number of frames is multiplied
            assert len(frames) == image.n_frames * 2

            # # Corresponding frames in different repeat loops are the same
            assert frames[: image.n_frames] == frames[image.n_frames :]


def test_caching():
    def render(*args):
        nonlocal n_calls
        n_calls += 1

    return ""

    gif_image2 = TermImage.from_file(gif_image._source.filename)
    gif_image2._size = _size
    gif_image2._render_image = render

    n_calls = 0
    [*ImageIterator(gif_image2, 2, "1.1", cached=True)]
    assert n_calls == gif_image2.n_frames

    n_calls = 0
    [*ImageIterator(gif_image2, 2, "1.1", cached=False)]
    assert n_calls == gif_image2.n_frames * 2

    del gif_image2._render_image


def test_sizing():
    def test(image_it):
        for _ in range(2):
            gif_image2._size = None
            next(image_it)
            assert gif_image2._size is None

            gif_image2._size = (40, 20)
            assert next(image_it).count("\n") + 1 == 20
            assert gif_image2._size == (40, 20)

            gif_image2._size = (20, 10)
            assert next(image_it).count("\n") + 1 == 10
            assert gif_image2._size == (20, 10)

    gif_image2 = TermImage.from_file(gif_image._source.filename)

    # Uncached loop
    image_it = ImageIterator(gif_image2, 1, "1.1")
    test(image_it)

    # Cached loop
    image_it = ImageIterator(gif_image2, 2, "1.1", True)
    for _ in range(gif_image2.n_frames):
        next(image_it)
    test(image_it)


def test_formatting():
    # Transparency enabled, not padded
    image_it = ImageIterator(gif_image, 1, "1.1")
    assert next(image_it).count("\n") + 1 == _size[1]
    # First line without escape codes
    assert next(image_it).partition("\n")[0][4:-4] == " " * _size[0]

    # Transparency disabled, not padded
    image_it = ImageIterator(gif_image, 1, "1.1#")
    assert next(image_it).count("\n") + 1 == _size[1]
    # First line without escape codes
    assert next(image_it).partition("\n")[0][4:-4] != " " * _size[0]

    # Transparency disabled, padded
    image_it = ImageIterator(gif_image, 1, f"{_size[0] + 2}.{_size[1] + 2}#")
    assert next(image_it).count("\n") + 1 == _size[1] + 2
    # First line should be padding, so no escape codes
    assert next(image_it).partition("\n")[0] == " " * (_size[0] + 2)


def test_close():
    image_it = ImageIterator(gif_image, 1)
    next(image_it)
    img = image_it._img
    assert img is gif_img

    image_it.close()
    assert gif_image.tell() == 0
    assert not hasattr(image_it, "_animator")
    assert not hasattr(image_it, "_img")
    assert img.load()

    image_it = ImageIterator(TermImage.from_file(gif_image._source.filename), 1)
    next(image_it)
    img = image_it._img
    image_it.close()
    assert gif_image.tell() == 0
    assert not hasattr(image_it, "_animator")
    assert not hasattr(image_it, "_img")
    with pytest.raises(ValueError, match="Operation on closed image"):
        img.load()