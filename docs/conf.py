project = "Sediment"
copyright = "2026, HypeProof"
author = "HypeProof"

extensions = [
    "myst_parser",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "alabaster"
html_title = "Sediment Docs"
html_short_title = "Sediment"
html_copy_source = False
html_show_sourcelink = False
html_extra_path = [
    "architecture-diagram.html",
    "system-flow.html",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "substitution",
    "tasklist",
]

suppress_warnings = [
    "myst.header",
]
