"""Contract tests for dependency hardening modernization spec.

All tests are marked xfail — they should pass after Phase 2 implementation
adds version constraints, security tooling, narrows exception catches,
and removes the migration shim.
"""

import inspect
import re

import pytest

REQUIRED_DEPS = {
    "llm": "0.31",
    "httpx": "0.28.1",
    "httpx-sse": "0.4.3",
    "pathspec": "1.1.1",
    "sqlite-utils": "3.39",
    "rich": "15.0.0",
    "pygments": "2.20.0",
    "pydantic": "2.13.3",
}


# ── 1. Dependency Version Constraints ────────────────────────────


@pytest.mark.xfail(reason="modernization contract")
def test_all_deps_have_version_constraints(pyproject_toml: dict) -> None:
    """Every declared dependency must have a version specifier."""
    deps = pyproject_toml["project"]["dependencies"]
    for dep in deps:
        assert ">=" in dep or ">" in dep or "==" in dep or "~=" in dep, (
            f"Dependency '{dep}' has no version constraint"
        )


@pytest.mark.xfail(reason="modernization contract")
@pytest.mark.parametrize("dep_name,min_version", list(REQUIRED_DEPS.items()))
def test_dep_minimum_version(pyproject_toml: dict, dep_name: str, min_version: str) -> None:
    """Each dependency must declare a minimum version >= the specified floor."""
    deps = pyproject_toml["project"]["dependencies"]
    matching = [
        d
        for d in deps
        if d.split(">=")[0].split(">")[0].split("==")[0].strip().lower() == dep_name.lower()
    ]
    assert len(matching) == 1, f"Expected exactly one entry for '{dep_name}', found: {matching}"
    dep_spec = matching[0]
    assert ">=" in dep_spec, f"Dependency '{dep_spec}' must use >= constraint"
    declared_version = dep_spec.split(">=")[1].strip()
    assert declared_version == min_version, (
        f"'{dep_name}' version constraint is '{declared_version}', expected '>={min_version}'"
    )


# ── 2. Security Audit Tooling ────────────────────────────────────


@pytest.mark.xfail(reason="modernization contract")
def test_pip_audit_in_dev_deps(pyproject_toml: dict) -> None:
    """pip-audit must be in the dev dependency group with a version constraint."""
    dev_deps = pyproject_toml["dependency-groups"]["dev"]
    matching = [d for d in dev_deps if "pip-audit" in d.lower()]
    assert len(matching) == 1, f"Expected one pip-audit entry, found: {matching}"
    assert ">=" in matching[0] or ">" in matching[0], (
        f"pip-audit must have a version constraint, got: '{matching[0]}'"
    )


# ── 3. Narrow Exception Catches ──────────────────────────────────


@pytest.mark.xfail(reason="modernization contract")
def test_no_broad_exception_in_render_code() -> None:
    """llm_fcio.py must not contain 'except Exception' or noqa: BLE001 annotations."""
    import llm_fcio

    source = inspect.getsource(llm_fcio)
    # Check for bare "except Exception" patterns
    matches = re.findall(r"except\s+Exception\b", source)
    assert len(matches) == 0, f"Found {len(matches)} 'except Exception' in llm_fcio.py"


@pytest.mark.xfail(reason="modernization contract")
def test_no_ble001_noqa_annotations() -> None:
    """llm_fcio.py must not contain noqa: BLE001 suppression comments."""
    import llm_fcio

    source = inspect.getsource(llm_fcio)
    matches = re.findall(r"#\s*noqa:\s*BLE001", source)
    assert len(matches) == 0, f"Found {len(matches)} 'noqa: BLE001' in llm_fcio.py"


# ── 4. Migration Shim Removal ────────────────────────────────────


@pytest.mark.xfail(reason="modernization contract")
def test_no_string_migration_shim() -> None:
    """_load_models must not contain code migrating string-only cache to dict format."""
    from llm_fcio import _load_models

    source = inspect.getsource(_load_models)
    assert "isinstance(data[0], str)" not in source, (
        "Migration shim (string-to-dict conversion) still present in _load_models"
    )


@pytest.mark.xfail(reason="modernization contract")
def test_no_migration_write_back() -> None:
    """_load_models must not write back migrated data to cache."""
    from llm_fcio import _load_models

    source = inspect.getsource(_load_models)
    assert "p.write_text" not in source, (
        "Cache write-back in _load_models suggests migration shim is still present"
    )
