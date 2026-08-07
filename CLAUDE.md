# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`action0-service` is a lightweight service registry / dependency injection framework: services are registered as classes, factories, or instances — by type and/or name — and resolved with constructor injection, scoped lifetimes, and optional YAML service catalogs. The core has zero runtime dependencies; `Registry.load_yaml()` needs the optional `yaml` extra (PyYAML). It ships the `action0.service` package (`action0` is a PEP 420 namespace package) from a `src/` layout, is built with hatchling, and uses `uv` for environment/dependency management.

## Rules

- **Never commit without asking.** Also never push, tag, or publish on your own.
- **Discuss first.** Always present the plan and the intended edits and get agreement before changing files.
- Every code change comes with: tests, docstrings, inline comments where the code isn't self-explanatory, and updated usage examples in `README.md` and the Sphinx docs (`docs/usage.md`).
- Before considering work done, run ruff, mypy, pyright, ty and pytest (commands below) and fix what they report.
- Supported Python versions: 3.11 up to the latest release. Don't use syntax or stdlib features introduced after 3.11, and don't rely on behavior removed in newer versions.

## Commands

`uv run` syncs the environment automatically (the dev dependency group is installed by default), so no separate install step is needed.

```sh
uv run pytest                                        # all tests
uv run pytest tests/action0/service/test_registry.py # one file
uv run pytest tests/action0/service/test_registry.py::LookupTestCase::test_get_by_name  # one test

uv run ruff check      # lint (add --fix to autofix)
uv run ruff format     # format
uv run mypy            # type-check (strict; files are configured in pyproject.toml)
uv run pyright         # type-check
uv run ty check        # type-check

uv run --group docs sphinx-build -W --keep-going -b html docs docs/_build/html  # build docs

uv build               # build sdist + wheel into dist/
```

`pytest` also runs the `>>>` examples in the docstrings as doctests (`--doctest-modules` over `src/`), so docstring examples must produce their shown output exactly.

## Architecture

Modules under `src/action0/service/`:

- `registry.py` — `Registry`, the central container. Registration (`register()`, `register_instance()`, `@service` decorator, `register_scope()`, `load_yaml()`), lookup (`get()` by type — subclass-aware — or name, `find()`, `get_all()`, `build()` for unregistered classes, `@inject` for functions, `override()` for tests), and lifecycle (`warmup()` for eager singletons, `validate()` for static config checking including cycle detection, `close()` disposing managed instances in reverse creation order; registries are context managers). Registries can be layered via `parent=`; caching scopes build instances in the *owning* registry's context so shared singletons never capture child registrations, while transient services build with the requesting registry's wiring. Cycle detection uses a module-level per-thread stack so chains across parent/child registries are caught.
- `definitions.py` — `Definition` (one registration: provider, provides, name, scope key, params, flags; hashes by identity), `AnonymousFactory` (nested YAML factories), and introspection: `provider_spec()` (cached signature + resolved hints), `infer_provides()`, `unwrap_annotation()` (peels `Annotated`/`Optional`, extracts `Named` qualifiers). `NON_INJECTABLE_TYPES` lists builtin value types that are never injected by bare annotation.
- `scopes.py` — `Scope` enum (`singleton`, `transient`, `thread`, `context`) and the `ScopePolicy` implementations; custom scopes subclass `ScopePolicy` (set `caches = False` for non-storing scopes). A single module-level re-entrant creation lock makes singleton creation race-free without lock-ordering deadlocks.
- `markers.py` — `Named` (Annotated qualifier), `Ref` (late-bound service reference in params), `injected` (sentinel default for `@inject`).
- `loader.py` — YAML catalog loading (imported lazily; requires PyYAML). Reserved keys `factory`/`scope`/`provides`/`default`/`eager`/`params`; every other key is a constructor param (`name` deliberately included — cipopo-style catalogs pass it to the factory). `!ENV` substitutes `${VAR}`/`${VAR:-fallback}` at load time (strings only, never re-parsed), `!ref` injects other services at build time, mappings containing `factory` become anonymous fresh instances, `.`-prefixed top-level keys are anchor-only templates.
- `errors.py` — exception hierarchy, everything derives from `ServiceError`.

Injection resolution order for a constructor parameter: configured `params` value → registered service matching the annotation (a `Named` qualifier forces name lookup; builtin value types are skipped) → the provider's own default → `None` for `Optional[...]` → `InjectionError`. A matching registration deliberately beats the provider's default.

Conventions:

- The version is single-sourced as `__version__` in `src/action0/service/__init__.py`; hatch extracts it with the regex in `[tool.hatch.version]`. Bump it only there.
- Releases: pushing a `vX.Y.Z` tag triggers `.github/workflows/release.yml`, which re-runs all checks, verifies the tag matches `__version__`, builds, and publishes to PyPI via trusted publishing (environment `pypi`). Never bump the version, tag, or publish on your own — releasing is the user's call.
- Tests mirror the `src/` layout under `tests/action0/service/` and are `unittest.TestCase` classes, executed via pytest. The loader tests generate an importable module into a temp dir at import time (factories must be importable by dotted path).
- Ruff enforces one import per line (isort `force-single-line`), line length 99, `action0` as first-party.
- Docs live in `docs/` (Sphinx + Furo, MyST Markdown pages, autodoc for the API reference). Docstrings are Sphinx-reST (`:param:`, `:py:meth:` roles). CI builds them with `-W` on every run and deploys to GitHub Pages on pushes to `main`.
- The README carries an AI-usage disclosure section — keep it accurate when the development workflow changes.
