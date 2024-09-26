# Configuration file for the Sphinx documentation builder.
#
# For the full list of configuration options, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

from sphinx.ext.autodoc import DecoratorDocumenter, FunctionDocumenter
from sphinx_toolbox.collapse import CollapseNode
from sphinxcontrib import prettyspecialmethods

from term_image import __version__, utils
from term_image.image.common import ImageMeta
from term_image.image.iterm2 import ITerm2ImageMeta

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
# sys.path.insert(0, os.path.abspath("../../src"))

# -- Project information -----------------------------------------------------
project = "Term-Image"
copyright = "2022, Toluwaleke Ogundipe"
author = "Toluwaleke Ogundipe"
release = __version__

# -- General configuration ---------------------------------------------------
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_toolbox.decorators",
    "sphinx_toolbox.github",
    "sphinx_toolbox.sidebar_links",
    "sphinx_toolbox.more_autosummary",
    "sphinx_toolbox.collapse",
    "sphinx_toolbox.more_autodoc.typevars",
    "sphinxcontrib.prettyspecialmethods",
]

# -- Warnings ----------------------------------------------------------------
suppress_warnings = [
    # `autosummary` issues a plethora of this warning.
    # See https://github.com/sphinx-doc/sphinx/issues/12589.
    # NOTE: Check back later.
    "autosummary.import_cycle",
]

# -- Options for HTML output -------------------------------------------------
html_theme = "furo"
html_logo = "resources/logo.png"
html_favicon = "resources/logo.ico"

# -- Options for extensions ----------------------------------------------

# # -- sphinx-autodoc -----------------------------------------------------
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
    "member-order": "bysource",
    "autosummary": True,
    "autosummary-nosignatures": True,
}
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"
autodoc_member_order = "bysource"
autodoc_inherit_docstrings = False

# # -- sphinx-intersphinx ----------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pillow": ("https://pillow.readthedocs.io/en/stable", None),
    "requests": ("https://requests.readthedocs.io/en/stable", None),
    "typing_extensions": ("https://typing-extensions.readthedocs.io/en/stable", None),
    "urwid": ("https://urwid.org", None),
}

# # -- sphinx_toolbox-github ----------------------------------------------
github_username = "AnonymouX47"
github_repository = "term-image"

# # -- sphinx_toolbox-more_autosummary ----------------------------------------------
autodocsumm_member_order = "bysource"

# -- Event handlers -------------------------------------------------------------

# # -- autodocsumm -----------------------------------------------------------


def autodocssumm_grouper(app, what, name, obj, section, parent):
    from enum import EnumMeta
    from types import FunctionType

    # Documented members with only annotations but no value
    # i.e not in the parent's `__dict__` (or `__dir__` ?)
    if not name:
        # raise Exception(f"{what=}, {obj=}, {section=}, {parent=}")
        return

    if isinstance(obj, EnumMeta):
        return "Enumerations"
    if isinstance(obj, type) and issubclass(obj, Warning):
        return "Warnings"
    if isinstance(parent, type):
        short_name = name.rpartition(".")[2]
        # Can't use `getattr()` because of data descriptors that may also be defined
        # on the metaclass (such as with `Class[Instance]Property`)
        for cls in parent.mro():
            try:
                obj = vars(cls)[short_name]
                break
            except KeyError as e:
                err = e
        else:
            raise err

        if isinstance(obj, utils.ClassProperty):
            return "Class Properties"
        if isinstance(obj, utils.ClassInstanceProperty):
            return "Class/Instance Properties"
        if isinstance(obj, property):
            return "Instance Properties"
        if isinstance(obj, FunctionType):
            return (
                "Special Methods"
                if name.startswith("__") and name.endswith("__")
                else "Instance Methods"
            )
        if isinstance(obj, utils.ClassInstanceMethod):
            return "Class/Instance Methods"
        if isinstance(obj, classmethod):
            return "Class Methods"
        if isinstance(obj, staticmethod):
            return "Static Methods"
        if name.startswith("__") and name.endswith("__"):
            return "Special Attributes"


# -- Extras -----------------------------------------------------------

# The properties defined by the metaclass' would be invoked instead of returning the
# property defined by the class
for meta in (ImageMeta, ITerm2ImageMeta):
    for attr, value in tuple(vars(meta).items()):
        if isinstance(value, utils.ClassPropertyBase):
            delattr(meta, attr)

# # -- autodecorator -------------------------------------------------------------


@classmethod
def can_document_member(cls, member, *args, **kwargs):
    return hasattr(member, "_no_redecorate_wrapped_") and (
        super(DecoratorDocumenter, cls).can_document_member(member, *args, **kwargs)
    )


DecoratorDocumenter.priority = FunctionDocumenter.priority + 5
DecoratorDocumenter.can_document_member = can_document_member

# # -- prettyspecialmethods ------------------------------------------------------


def reflected_binary_op_transformer(op):
    from sphinxcontrib.prettyspecialmethods import Text, emphasis, inline, patch_node

    def xf(name_node, parameters_node):
        return inline(
            "",
            "",
            emphasis("", parameters_node.children[0].astext()),
            Text(" "),
            patch_node(name_node, op, ()),
            Text(" "),
            emphasis("", "self"),
        )

    return xf


def skip_undoc_special_methods(*args, **kwargs):
    pass


prettyspecialmethods.SPECIAL_METHODS["__ror__"] = reflected_binary_op_transformer("|")
prettyspecialmethods.show_special_methods = skip_undoc_special_methods

# # -- sphinx_toolbox.collapse -------------------------------------------------

# Fixes some weird `AttributeError` when building on `ReadTheDocs`
CollapseNode.label = None

# -- Setup Function --------------------------------------------------------------------


def setup(app):
    app.connect("autodocsumm-grouper", autodocssumm_grouper)
