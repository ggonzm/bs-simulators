"""Sphinx configuration for the public bs-simulators documentation."""

from pathlib import Path
import os
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
project = "bs-simulators"
author = "SUPPRESS Research Group"
copyright = "2026, SUPPRESS Research Group"
release = version = metadata["version"]
language = "en"
os.environ.setdefault("MPLBACKEND", "Agg")
extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.napoleon",
              "sphinx.ext.mathjax", "sphinx.ext.doctest"]
myst_enable_extensions = ["dollarmath", "colon_fence"]
myst_heading_anchors = 3
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
autodoc_typehints = "none"
autodoc_member_order = "bysource"
napoleon_numpy_docstring = True
napoleon_google_docstring = False
html_theme = "pydata_sphinx_theme"
html_title = project
html_theme_options = {"navigation_depth": 3, "header_links_before_dropdown": 4,
                      "show_prev_next": False,
                      "github_url": "https://github.com/ggonzm/bs-simulators"}
html_baseurl = os.environ.get("READTHEDOCS_CANONICAL_URL", "")
html_show_sourcelink = False
