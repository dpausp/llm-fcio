---
lifecycle:
  requirements: 
  design: 
  plan: 
  workflow: 
  verify: 
---

# quality-elevation

## Context

Quality audit scored llm-fcio at 23/100 (Grade F). Eight elevation items were approved for implementation: custom exceptions, ruff configuration, type annotations, test infrastructure, nesting reduction, dead code forwarding, subprocess hardening, and httpx status code constants. All items target the single source file `llm_fcio.py` (817 lines) and `pyproject.toml`.

## Decisions

### custom-exceptions

#### Context

11 `raise click.ClickException(...)` calls span two semantic categories: API communication failures and model resolution failures. Mixing them in a single generic type prevents callers from distinguishing error categories.

#### Decision

Create `ModelError(Exception)` and `ApiError(Exception)` as custom exception classes. Model-related raises (ambiguous/unknown model) use `ModelError`. API-related raises (empty response, streaming error, status errors in `api_request`) use `ApiError`. User-facing validation errors (missing prompt, path not found, no files, aborted) remain `click.ClickException` since they are CLI-user-facing, not programmatic.

#### Consequences

Callers can catch specific error categories. Exception names do NOT use "Rzob" prefix. Both classes inherit from `Exception` (not `click.ClickException`) to separate programmatic errors from CLI UX.

### ruff-strict-config

#### Context

No ruff configuration exists. Baseline is clean (0 issues) but extreme mode reveals 178 suppressed issues including critical hiding (BLE001, B904, C901).

#### Decision

Add `[tool.ruff]` and `[tool.ruff.lint]` sections to `pyproject.toml` with explicit rule selection: `["E", "F", "I", "N", "W", "UP", "B", "C4", "SIM", "TCH", "BLE", "B904"]`. Target version `py314`, line length 100. Ignore `E501` (line length handled by formatter). ANN rules NOT enabled yet (gradual adoption). Add `[tool.ruff.format]` with `quote-style = "double"`.

#### Consequences

All new code is held to the configured standard. Existing code may need adjustments to pass the stricter rules. BLE and B904 ensure the tidy fixes stay permanent.

### type-annotations

#### Context

28 annotation violations: 19 missing return types, 9 missing parameter types across public functions, Click closures, and hookimpl callbacks.

#### Decision

Add return type annotations to all 19 functions. Add parameter types to all 9 untyped parameters. For `llm` framework types (`prompt`, `response`, `conversation`, `register`), use the types available from `llm` package imports. For Click callbacks, use `click.Context`, `click.Group` etc.

#### Consequences

Full type coverage enables future `ty` integration. Function signatures become self-documenting.

### test-infrastructure

#### Context

Zero test coverage. No tests directory, no pytest config, no test dependencies. Three pure functions identified as testable without mocking: `_chunk_lines`, `_discover_files`, `_build_chat_body`.

#### Decision

Create `tests/` directory with `conftest.py`. Add `pytest` and `pytest-cov` to `[dependency-groups]` under `dev` group. Add `[tool.pytest.ini_options]` to `pyproject.toml` with `addopts = ["--cov=llm_fcio", "--cov-report=term-missing", "--durations=0"]` and `testpaths = ["tests"]`. Write initial tests for the three pure functions.

#### Consequences

CI-ready test infrastructure. Pure function tests provide immediate coverage without mocking complexity. `respx` NOT added yet (HTTP mocking deferred to when API tests are needed).

### nesting-reduction

#### Context

Two near-identical 7-level-deep SSE parsing loops: one in `RzobModel.execute()` (lines 211–235) and one in `_send_chat_request()` (lines 768–795). Both parse the same SSE format with the same `[DONE]` termination and delta extraction logic.

#### Decision

Extract a shared generator function `_iter_sse_content(client, url, headers, body) -> Iterator[str]` that encapsulates the SSE connection, iteration, JSON parsing, and delta extraction. Both `execute()` and `_send_chat_request()` call this generator instead of embedding the full SSE loop inline.

#### Consequences

Each function loses 15–20 lines of inline SSE logic. Nesting drops from 7 to ~3 levels. The duplicated logic is eliminated.

### dead-code-forwarding

#### Context

`tools` and `response_format` are declared in `RzobModel.Options` (lines 153–161) with pydantic `Field()` but never read in `execute()`. Users can set these options but they are silently ignored.

#### Decision

Forward both options in `execute()` body after the existing `top_p` forwarding block (after line 204): `if prompt.options.tools is not None: body["tools"] = prompt.options.tools` and similarly for `response_format`.

#### Consequences

Tool calling and JSON mode become functional. No API contract change — just stops silently swallowing user intent.

### subprocess-hardening

#### Context

`subprocess.run(["fzf", ...])` at line 100 uses partial binary name (S607), no return code check, and no timeout. `shutil` is not imported.

#### Decision

Import `shutil`. Pre-check with `shutil.which("fzf")` before subprocess call. Add explicit `check=False` to subprocess.run. Add `timeout=10` parameter.

#### Consequences

Eliminates S607 lint finding. Timeout prevents hanging. `shutil.which` catches missing/non-executable fzf before subprocess attempt.

### httpx-status-codes

#### Context

6 locations use raw integer HTTP status codes (400, 401, 404). Line 582 does a string-match hack `"400" in str(e)`.

#### Decision

Replace raw integers with `httpx.codes.BAD_REQUEST`, `httpx.codes.UNAUTHORIZED`, `httpx.codes.NOT_FOUND`. For the generic threshold check (line 65, `>= 400`), use `>= httpx.codes.BAD_REQUEST`. For the string-match hack (line 582), check the exception's context or status_code attribute instead.

#### Consequences

Named constants are self-documenting. The string-match hack is eliminated.

## References

- Quality audit report: `.agents/reports/quality-audit.md`
- Course corrections: `.agents/tmp/quality/analysis/course-corrections.md`
- North Star: `.agents/tmp/quality/analysis/north-star.md`
- Dev docs: `docs/dev/architecture.md`
