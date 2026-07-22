project = "Sediment"
copyright = "2026, HypeProof"
author = "HypeProof"

from pygments.lexers.special import TextLexer

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
html_title = "Sediment 문서"
html_short_title = "Sediment"
html_copy_source = False
html_show_sourcelink = False

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


def setup(app):
    for lexer_name in ["mermaid", "jsonc", "json", "http", "sql"]:
        app.add_lexer(lexer_name, TextLexer)
