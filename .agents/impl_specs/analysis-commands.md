---
lifecycle:
  requirements:
    completed_at: "2026-05-21T17:28:02Z"
    git_rev: "753409e"
  design:
    completed_at: "2026-05-21T18:54:03Z"
    git_rev: "753409e"
  plan:
    completed_at: "2026-05-21T19:30:00Z"
    git_rev: "2a0fe1e"
---

# analysis-commands

## Context

Developer-facing analysis commands and a streaming markdown renderer for the llm-fcio plugin, built on llm's native prompt pipeline (templates, fragments, model.prompt).

## Decisions

### approach

#### Context

The existing fcio chat command bypasses llm's prompt pipeline. New features must not repeat that pattern.

#### Decision

Build convenience commands as wrappers composing llm primitives (model.prompt, Fragment, Template). No parallel infrastructure.

#### Alternatives

a. Standalone commands duplicating llm features — rejected: loses template/schema/conversation support

#### Consequences

Template, schema, tool, and conversation support for free. Minimal plugin code.

### command-interface

#### Context

Developers need short, memorable commands for common analysis tasks.

#### Decision

`llm fcio analyze [review|overview] [files...] --model MODEL`. Positional: type (review|overview), files (globs). Zero-config: no args = auto-detect code files in CWD. Only `--model` flag in v1.

#### Alternatives

a. Flag-based (--glob, --type) — rejected: more typing, harder to discover
b. Template-only — rejected: no file collection or zero-config behavior

#### Consequences

Extensible to more analysis types. Zero args = immediate value. See decision `zero-config-defaults` for auto-detection behavior.

### renderer-hook

#### Context

Rich streaming output (syntax-highlighted markdown blocks) should work for all models, not just fcio ones.

#### Decision

Monkey-patch llm.Response.__iter__ at plugin load to route streaming through the existing _StreamingRenderer. TTY-guarded and fallback-safe (see decision `renderer-safety`).

#### Alternatives

a. Renderer only in fcio commands — rejected: limits benefit to fcio models only
b. Separate plugin — rejected: installation friction
c. Wrapper command — rejected: changes invocation pattern

#### Consequences

Single install gives rich rendering everywhere. Risk: breaks if llm changes Response internals.

### renderer-safety

#### Context

Monkey-patching core llm classes must not break automation or pipelines.

#### Decision

Activate only when sys.stdout.isatty(). In non-TTY: original behavior unchanged. Try/except around renderer — on failure, fall back to original __iter__.

#### Alternatives

a. Always patch + opt-out flag — rejected: unsafe default for automation
b. Version check llm internals — rejected: brittle, adds coupling

#### Consequences

Safe in pipes, redirects, CI. Graceful degradation on renderer failure. Invisible to the user when it degrades.

### template-system

#### Context

Analysis prompts need to be version-controlled, type-safe, and available via llm's native template discovery.

#### Decision

Templates as Python constants in a TEMPLATES dict. Register via register_template_loaders("fcio", loader). Loader maps name to llm.Template instances. Templates available as `llm -t fcio:review`.

#### Alternatives

a. YAML files — rejected: parsing overhead, no type safety
b. Hardcoded system prompts — rejected: not discoverable, no template system integration

#### Consequences

Type-safe, testable, version-controlled. Exercises an unused llm hook. Discoverable via `llm templates list`.

### file-collection

#### Context

Analysis commands need predictable, fast code file detection.

#### Decision

Extension whitelist for code files. .gitignore filtering via pathspec (existing dependency). Collected files become llm.Fragment objects with source paths.

#### Alternatives

a. Heuristic detection (content-based) — rejected: unpredictable, slow
b. git ls-files only — rejected: fails outside git repos

#### Consequences

Predictable, fast, easy to reason about. Works with .gitignore conventions.

### zero-config-defaults

#### Context

First-time users need immediate value without reading documentation.

#### Decision

No args: auto-detect code files in CWD (see decision `file-collection`), display file list with sizes and token estimate (chars/4 heuristic), then send to model. No files found: clear error with actionable hints, exit 1.

#### Alternatives

a. Require explicit args — rejected: friction for common case
b. Prompt for confirmation — rejected: breaks non-interactive use

#### Consequences

Immediate value on first run. Transparent about what context the model receives.

### test-strategy

#### Context

New code paths need coverage without slowing exploratory development.

#### Decision

Extend existing Click CliRunner tests. Tests-after approach. Mock model.prompt to avoid API calls. Categories: file collection unit tests, template loader tests, analyze command E2E, renderer monkey-patch safety.

#### Alternatives

a. TDD — rejected: slows exploratory phase
b. No tests — rejected: no safety net for refactors

#### Consequences

Coverage for all new code paths. Tests serve as living documentation of command contracts.

## Requirements

### Interface Contracts

Usage examples:

- `llm fcio analyze` — analyze current project (auto-detect)
- `llm fcio analyze review src/**/*.py` — review specific files
- `llm fcio analyze overview --model 120b` — project overview with specific model
- `llm -t fcio:review` — use review template directly

Discovery:

- `llm fcio analyze --help` — shows types and examples
- `llm templates list` — shows fcio-prefixed templates

### Error Communication

| Condition | Behavior |
|---|---|
| No code files found | "No code files found in \<dir\>" + hints (specify files, check extensions) + exit 1 |
| Model unavailable | Propagate llm's model resolution error |
| Renderer failure | Fallback to raw output, transparent degradation |

## Appendix

```yaml
implementation_plan:
  id: analysis-commands
  description: "Developer-facing analysis commands (review/overview) and streaming markdown renderer hook for llm-fcio plugin"
  git_rev: "2a0fe1e"
  created_at: "2026-05-21T19:30:00Z"
  target_tests:
    - file: tests/impl_spec/test_analysis_commands.py
      tests:
        - test_file_collection_includes_python_files
        - test_file_collection_excludes_non_code_extensions
        - test_file_collection_respects_gitignore
        - test_file_collection_returns_path_objects
        - test_file_collection_empty_directory
        - test_templates_dict_contains_review
        - test_templates_dict_contains_overview
        - test_template_loader_registered_as_fcio
        - test_template_loader_returns_llm_template_instances
        - test_templates_available_via_fcio_prefix
        - test_analyze_help_shows_types_and_examples
        - test_analyze_auto_detects_code_files_in_cwd
        - test_analyze_review_with_specific_files
        - test_analyze_overview_with_model_flag
        - test_analyze_no_code_files_error_message
        - test_analyze_no_code_files_shows_actionable_hints
        - test_analyze_displays_file_sizes_and_token_estimate
        - test_analyze_invalid_analysis_type
        - test_renderer_patch_only_active_when_tty
        - test_renderer_patch_not_applied_when_not_tty
        - test_renderer_failure_falls_back_to_original_iter
        - test_renderer_patch_transparent_degradation
```
