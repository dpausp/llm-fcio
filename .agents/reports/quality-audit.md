# Quality Audit Report

## Human Summary

Quality meta-audit of `llm-fcio` (v2026.5.19a1), a single-file `llm` CLI plugin (1582 lines). All baseline quality gates were green (tox lint/type/cov, ruff, ty, 97% coverage). The meta-audit revealed a well-configured project with strong tooling: tox provides a single-command gate, ruff has a deliberate 18-rule selection, and pytest runs with coverage+durations by default. The main finding was 12 stale xpass tests in `test_modernize_deps.py` (5 fulfilled modernization contracts whose xfail markers were never removed). A pre-existing Hypothesis flake was also exposed and fixed. Both fixes applied; all gates green after fix loop.

## Completion Checklist
- [x] Entry point inventory + smoke test completed
- [x] Structural inventory completed (noqa, mock, complexity, test discovery, dependencies)
- [x] Quality gates collected (baseline + extreme)
- [x] All 4 investigation streams completed with structured review results
- [x] Tool tolerance audit produced with per-tool signals (ruff/ty/pytest)
- [x] Test collection integrity verified (all test files collected, no config hiding)
- [x] Skip/xfail/xpass audit completed (lazy skips flagged, cross-platform checked)
- [x] Test double strategy analyzed (mock:fake:golden:real per layer)
- [x] E2E coverage assessed for every entry point (PROVEN/SUSPECTED/UNKNOWN/BROKEN)
- [ ] Full CLI test not triggered — existing E2E evidence sufficient
- [x] Fixes applied for critical findings (stale xfail, Hypothesis flake)
- [x] Fix loop completed (gates green)
- [x] North Star generated from loaded skills
- [x] Course Corrections derived (Reality vs North Star diff)
- [ ] Git commit: pending

## Entry Point Inventory
| Entry Point | Type | Source | Smoke | E2E Status | Evidence |
|-------------|------|--------|-------|------------|----------|
| register_models | plugin-hook | llm_fcio.py:621 | N/A | SUSPECTED | 5 tests (test_integration.py) via respx |
| register_embedding_models | plugin-hook | llm_fcio.py:638 | N/A | SUSPECTED | 2 tests (test_integration.py) via respx |
| register_template_loaders | plugin-hook | llm_fcio.py:681 | N/A | SUSPECTED | 3 tests (test_analysis_commands.py) via MagicMock |
| register_commands | plugin-hook | llm_fcio.py:1511 | N/A | SUSPECTED | 1 test (test_live.py, gated) |
| llm fcio refresh | cli-subcommand | llm_fcio.py:857 | PASS | PROVEN | test_cli.py + smoke test (6 models cached) |
| llm fcio models | cli-subcommand | llm_fcio.py:884 | PASS | PROVEN | test_cli.py (9 tests) + smoke test |
| llm fcio chat | cli-subcommand | llm_fcio.py:945 | PASS | SUSPECTED | 15 tests via CliRunner+respx |
| llm fcio embed | cli-subcommand | llm_fcio.py:1009 | PASS | SUSPECTED | 5 tests via CliRunner+respx |
| llm fcio capabilities | cli-subcommand | llm_fcio.py:1043 | PASS | SUSPECTED | 6 tests via CliRunner+respx |
| llm fcio simulate | cli-subcommand | llm_fcio.py:1191 | PASS | PROVEN | 4 tests, zero mocking (pure pipeline) |
| llm fcio tokens | cli-subcommand | llm_fcio.py:1294 | PASS | SUSPECTED | 3 tests via CliRunner+respx |
| llm fcio ingest | cli-subcommand | llm_fcio.py:1358 | PASS | SUSPECTED | 9 tests via CliRunner+respx+MagicMock |
| llm fcio analyze | cli-subcommand | llm_fcio.py:1456 | PASS | SUSPECTED | 8 tests via MagicMock |
| mdscream.py | script | scripts/mdscream.py | WARN | UNKNOWN | No --help, reads stdin |

## Tool Tolerance Audit
| Tool | Baseline | Extreme | Delta | Signal |
|------|----------|---------|-------|--------|
| ruff | 0 issues | 959 issues (ALL+preview) | 941 suppressed beyond config | 🟡 orange |
| ty | 0 errors | 0 errors (no --strict mode) | 0 | 🟢 green |
| pytest | 223 passed, 2 xfail, 0 xpass | 227 collected (no marker deselection) | 0 hidden | 🟢 green |

### ruff Delta Breakdown
- **Legitimate (94%)**: S101 (assert in tests), D-rules (docstrings), COM812 (format conflict), CPY001 (copyright), DOC-rules (doc sections), PT-rules (pytest style)
- **Questionable (5%)**: PLR2004 (magic values in tests), SLF001 (private member access on llm objects), FBT001 (bool positional args in click), ARG001 (unused callback args), PLC2701 (private imports in tests)
- **Critical hiding (<1%)**: S311 (random — false positive, demo only), PLW0603 (globals — pragmatic plugin pattern), S404/S603 (subprocess — safe, guarded by shutil.which)

## Test Collection Integrity
| Check | Result | Signal |
|-------|--------|--------|
| Tests on disk | 9 files | — |
| Tests collected | 9 files | — |
| Uncollected files | 0 — all files collected | 🟢 green |
| Collection errors | 0 | 🟢 green |
| Config exclusions | testpaths=["tests"], import_mode="importlib" | — |
| conftest hooks modifying collection | 1: pytest_collection_modifyitems (skips @live without --run-live) | 🟢 green |

- pytest config: addopts includes --cov=llm_fcio, --durations=0, --tb=short. No norecursedirs, no collect_ignore, no --ignore.
- Unaccounted test files: none — all files collected.

## Skip/Xfail/Xpass Audit
| Category | Count | Signal |
|----------|-------|--------|
| @pytest.mark.skip | 1 (live gating) | 🟢 green |
| @pytest.mark.xfail (modernization contract) | 2 (broad exception handling) | 🟡 orange |
| XPASS (before fix) | 12 → **0 after fix** | 🟢 green |
| Lazy skips | 0 | 🟢 green |
| Flaky-hidden | 0 | 🟢 green |
| Stale temporal skips | 0 | 🟢 green |

- Cross-platform skip asymmetry: none (no platform-specific tests)
- 5 stale xfail markers removed (contracts fulfilled): version constraints, pip-audit, migration shim
- 2 legitimate xfails remain: `test_no_broad_exception_in_render_code`, `test_no_ble001_noqa_annotations`

## Test Double Strategy
| Layer | Mock | Spec'd Mock | Fake | Golden | Real | Total |
|-------|------|-------------|------|--------|------|-------|
| Unit (test_pure_functions) | 0 | 0 | 0 | 0 | 40 | 40 |
| Unit (test_properties) | 0 | 0 | 0 | 0 | 13 | 13 |
| Integration (test_integration) | 0 | 0 | 0 | 0 | 39 | 39 |
| Integration (test_cli) | ~39 MagicMock | 0 | 0 | 0 | ~36 | 75 |
| Spec (impl_spec/) | 14 MagicMock | 0 | 0 | 0 | 38 | 52 |
| Renderer (test_streaming_renderer) | 0 | 0 | 0 | 0 | 13 | 13 |
| E2E (test_live) | 0 | 0 | 0 | 0 | 2 | 2 |

- Tautological tests (mock theater): 0 — no mock-asserts-mock pattern detected
- Golden file smell: 0 — no golden files used
- Mock density hotspots: `test_cli.py` (~39 MagicMock for llm.Collection), `test_analysis_commands.py` (14 MagicMock)
- 3 comments claim spec= incompatible with llm.Collection — **disproven** by live test, but low priority
- Overall double strategy verdict: Healthy — mocks concentrated in CLI layer only, pure functions and integration tests are mock-free
- Signal: 🟡 orange (bare mocks without spec=)

## Test Structure Summary
- Total tests: 227 collected (223 passed + 2 skipped + 2 xfailed)
- Distribution: unit 66, integration 114, spec/E2E 47
- RED FLAGS: 1.5/10 — NOT mock-only (respx transport mocking, real objects, property tests)
- Signal: 🟡 orange (bare mocks, no spec=)

## Test Coverage
| Module | Coverage | Missing Lines | Signal |
|--------|----------|---------------|--------|
| llm_fcio.py | 97% | 581, 584-599, 682, 759, 1483 | 🟢 green |

- Overall coverage: 97%
- Modules < 50%: none
- Entry points with 0% coverage: none
- Signal: 🟢 green

### Uncovered Lines Analysis
- Lines 581-599: `install_renderer_patch()` — optional renderer monkey-patch, conditional import
- Line 682: `fcio_template_loader()` — template loader registration guard
- Line 759: `_discover_files()` — exception handler for permission errors
- Line 1483: `cmd_analyze()` — early exit path

## Duration Anomalies
- Total suite time: ~5s
- Duration stats: P50=30ms, P90=110ms, P95=120ms, P99=140ms

| Category | Count | Details |
|----------|-------|---------|
| EXTREME OUTLIER (>P99+2σ) | 0 | none |
| FAKE SLOW (marked slow, <P50) | 0 | no slow markers |
| HIDDEN SLOW (unmarked, >P95) | 0 | all within range |
| Zero-duration (<1ms) | 0 | none |

- Slow test cluster: distributed (hypothesis tests are slowest at 0.14s)
- Root causes for outliers: N/A
- Signal: 🟢 green

## Dependency Audit
| Category | Count | Signal |
|----------|-------|--------|
| Forbidden libraries | 0 | 🟢 green |
| Stdlib reinvention | 0 | 🟢 green |
| Unused dependencies | 0 | 🟢 green |
| Missing blessed libraries | 0 | 🟢 green |
| Available but unused (partial migration) | 0 | 🟢 green |

- `click` imported directly but not declared — comes via `llm` transitive dependency. Acceptable for a plugin.
- All declared deps actively used in source.
- Signal: 🟢 green

## E2E Coverage Assessment
- PROVEN: 3 entry points (refresh, models, simulate)
- SUSPECTED: 10 entry points (chat, embed, capabilities, tokens, ingest, analyze, + 4 plugin hooks)
- UNKNOWN: 1 (mdscream.py script)
- BROKEN: 0
- Full CLI test triggered: NO — existing E2E evidence sufficient (smoke tests all PASS, respx transport-level mocking, 97% coverage)
- Signal: 🟡 orange

## Stream Signals
- Code Architecture: 🟠 orange (no enforcement, high complexity in `_make_client`)
- Code Quality: 🟢 green (all tools clean, justified suppressions)
- Test Structure: 🟠 orange (bare mocks, stale xfails fixed)
- E2E Coverage + Production Reality: 🟠 orange (77% SUSPECTED)

## Architectural North Star

| Dimension | True North | Source |
|-----------|------------|--------|
| HTTP Client | httpx (declared, used throughout) | python-dev skill |
| SSE | httpx-sse (declared, used for streaming) | project convention |
| CLI Framework | click via llm plugin (standard) | llm plugin convention |
| Display | rich (declared, used for output) | python-dev skill |
| Data Validation | pydantic (declared, used for Field) | python-dev skill |
| Database | sqlite-utils (declared, used for collections) | project convention |
| Test HTTP Mocking | respx (transport-level, best practice) | python-tests skill |
| Property Testing | hypothesis (declared, 13 tests) | python-tests skill |
| Linting | ruff with deliberate rule selection | python-dev skill |
| Type Checking | ty (strict) | python-dev skill |
| Task Runner | tox with uv (3 envs) | tox skill |

## Course Corrections

### NAV-1 Architecture Enforcement
- **Current heading:** No architecture enforcement (no pytest-archon, no test_architecture.py)
- **True north:** Layer boundaries enforced by automated rules
- **Correction:** Add pytest-archon rules for the single-file plugin pattern (if/when it grows)

### NAV-2 Mock Spec Usage
- **Current heading:** 15 bare MagicMock() without spec=, justified by stale "llm plugin incompatible" comments
- **True north:** All mocks use spec= for interface contract enforcement
- **Correction:** Add spec=llm.Collection to mock creations, remove stale justification comments

### NAV-3 E2E Test Coverage
- **Current heading:** 77% of entry points SUSPECTED (unit+respx only, no live invocation proof)
- **True north:** Every CLI subcommand has at least one E2E/integration test exercising the full path
- **Correction:** Add integration tests for chat, embed, capabilities that exercise CliRunner end-to-end

### NAV-4 Function Complexity
- **Current heading:** `_make_client` CC=47, `RzobModel.execute` CC=33, `_send_chat_request` CC=31
- **True north:** No function exceeds CC=15 (complexipy threshold)
- **Correction:** Refactor `_make_client` into smaller functions, extract streaming logic from `execute`

### NAV-5 Click Dependency Declaration
- **Current heading:** `click` imported directly but not declared in pyproject.toml (comes via llm)
- **True north:** All imports declared as explicit dependencies
- **Correction:** Add `click>=8.0` to project dependencies for explicitness

- NAV-items total: 5
- Dimensions on course (no deviation): 6 (coverage, duration, deps, suppressions, test collection, task runner)
- Signal: 🟡 orange

## Test Automation
- Task runner: tox (configured in pyproject.toml)
- Single-command gate: YES (`tox -p` runs lint + type + cov)
- Default coverage: full (no markers excluded, --cov in addopts)
- Signal: 🟢 green

## Critical Findings Fixed

1. **12 stale xpass tests** — Removed 5 fulfilled `@pytest.mark.xfail` decorators from `tests/impl_spec/test_modernize_deps.py`. The modernization contracts for version constraints, pip-audit, and migration shim removal were fulfilled but the markers were never cleaned up.

2. **Hypothesis test flake** — Fixed `test_chunk_lines_all_input_lines_covered` in `tests/test_properties.py` (line 80). Changed `splitlines()` to `split("\n")` because `splitlines()` is not the inverse of `'\n'.join()` — it silently drops trailing empty strings, causing the property to fail on edge cases like `text='\n'`.

## Full CLI Test Trace
Full CLI test not triggered — existing E2E evidence sufficient. All 9 CLI subcommands passed smoke tests (help + basic invocation). No crashes, no tracebacks.

## Code Volume
| File | Change |
|------|--------|
| tests/impl_spec/test_modernize_deps.py | Removed 5 `@pytest.mark.xfail` decorators + updated docstring |
| tests/test_properties.py | Line 80: `splitlines()` → `split("\n")` |

## Post-Fix Quality Gates
| Tool | Result |
|------|--------|
| tox (lint+type+cov) | ALL PASS (5.03s) |
| ruff check | 0 issues |
| ruff format | All formatted |
| ty check | 0 errors |
| pytest | 223 passed, 2 skipped, 2 xfailed, 0 xpass |
| coverage | 97% |
| E2E smoke | PASS |

## Recommendations
1. **Medium**: Add `spec=llm.Collection` to bare MagicMock creations in test_cli.py (remove 3 stale justification comments)
2. **Medium**: Refactor `_make_client` (CC=47) into smaller functions
3. **Low**: Add `click>=8.0` to explicit dependencies in pyproject.toml
4. **Low**: Add pytest-archon architecture rules if the plugin grows beyond single-file
5. **Low**: Consider running `test_live.py` in CI with a test API key for true E2E coverage

## Raw Data Location
`.agents/tmp/quality/` — inventory/, baseline/, extreme/, analysis/, e2e/
