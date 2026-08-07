"""Sphinx configuration for the action0-service documentation."""

from action0.service import __version__

project = "action0-service"
author = "Simon Lachinger"
copyright = "2026 Simon Lachinger"
release = __version__
version = __version__

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# ------------------------------------------------------------------- autodoc
autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
}
# sphinx-autodoc-typehints renders hints into the parameter descriptions
always_use_bars_union = True
typehints_defaults = "comma"

# ---------------------------------------------------------------------- myst
myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 3

# ---------------------------------------------------------------------- html
html_theme = "furo"
html_title = f"action0-service {__version__}"
