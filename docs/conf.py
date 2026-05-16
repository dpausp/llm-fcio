"""Sphinx configuration for llm-fcio."""

project = "llm-fcio"
author = "FCIO"
extensions = [
    "myst_parser",
    "autoapi.extension",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinx_llm.txt",
]

source_suffix = {
    ".md": "markdown",
    ".rst": "restructuredtext",
}

exclude_patterns = ["_build"]

# -- autoapi ---------------------------------------------------------------
autoapi_type = "python"
autoapi_dirs = [".."]
autoapi_ignore = [
    "*/.venv/*",
    "*/build/*",
    "*/dist/*",
    "*/egg-info/*",
    "*/__pycache__/*",
    "*/.ruff_cache/*",
    "*/docs/*",
]
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
]
autoapi_keep_files = True
autoapi_add_toctree_entry = True

# -- autodoc typehints -----------------------------------------------------
autodoc_typehints = "description"

# -- myst ------------------------------------------------------------------
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "tasklist",
]
myst_heading_anchors = 3

# -- HTML theme ------------------------------------------------------------
html_theme = "furo"
html_title = "llm-fcio Documentation"

# -- copybutton ------------------------------------------------------------
copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True
copybutton_line_continuation_character = "."

# -- sphinx-llm ------------------------------------------------------------
llms_txt_build_parallel = True
llms_txt_full_build = True


def _skip_member(
    app: object, what: str, name: str, obj: object, skip: bool, options: object
) -> bool | None:
    if name.startswith("_"):
        return True
    if what == "class" and "Test" in name:
        return True
    if (
        hasattr(obj, "__module__")
        and obj.__module__ is not None
        and obj.__module__.startswith("pydantic")
    ):
        return True
    return None


def _strip_pydantic_docstring(app: object, docname: str, source: list[str]) -> None:
    """Strip inherited Pydantic BaseModel docstring from autoapi RST."""
    if not docname.startswith("autoapi/"):
        return
    import re

    # The Options inner class inherits BaseModel's docstring which has
    # indented continuation lines invalid in RST context
    source[0] = re.sub(
        r"\n      A base class for creating Pydantic models\.\n.*?(?=\n      \.\. py:)",
        "\n",
        source[0],
        flags=re.DOTALL,
    )


def setup(app: object) -> None:
    app.connect("autoapi-skip-member", _skip_member)
    app.connect("source-read", _strip_pydantic_docstring)
