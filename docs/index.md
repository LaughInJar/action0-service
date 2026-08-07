# action0-service

A lightweight service registry and dependency injection framework:
register classes, factories, or ready-made instances — by type or by
name — and request them back with their constructor dependencies
resolved automatically. Scopes (singleton, transient, thread-local,
context-local), YAML service catalogs, and a test-friendly override
mechanism included. The core has zero dependencies.

Requires Python 3.11 or newer.

```shell
pip install action0-service          # or: uv add action0-service
pip install "action0-service[yaml]"  # with YAML catalog support (PyYAML)
```

## A taste

```python
from action0.service import Registry, Scope


class Database:
    def __init__(self, dsn: str = "sqlite://"):
        self.dsn = dsn


class UserRepository:
    def __init__(self, db: Database):  # injected from the registry
        self.db = db


registry = Registry()
registry.register(Database, params={"dsn": "postgres://db/app"})
registry.register(UserRepository)

repo = registry.get(UserRepository)
assert repo.db.dsn == "postgres://db/app"
assert registry.get(UserRepository) is repo  # singletons by default
```

There is no global registry and no import-time magic: a
{py:class}`~action0.service.registry.Registry` is an ordinary object you
create, fill, and pass around (or nest, see
[parent registries](registries.md)).

## Where to go next

- [Registering services](registration.md) — classes, factories,
  instances, names, and the `@registry.service` decorator.
- [Looking services up](lookup.md) — lookups by type (subclass-aware)
  and by name, and how ambiguity is resolved.
- [Injection](injection.md) — the exact rules for filling constructor
  parameters, the `Named` qualifier, `Ref` values, and the
  `@registry.inject` function decorator.
- [Scopes](scopes.md) — singleton, transient, thread, context, and
  writing custom scopes.
- [Parent registries and overrides](registries.md) — layered wiring for
  requests and tests.
- [YAML service catalogs](yaml.md) — the full file format, `!ENV` and
  `!ref` tags.
- [Lifecycle](lifecycle.md) — `validate()`, `warmup()`, `close()`.
- [API reference](api.md) — every public class and function.

```{toctree}
:hidden:

registration
lookup
injection
scopes
registries
yaml
lifecycle
api
```

## Project

- Source: <https://github.com/LaughInJar/action0-service>
- Issues: <https://github.com/LaughInJar/action0-service/issues>
- PyPI: <https://pypi.org/project/action0-service/>
- License: MIT

This library is developed with heavy use of AI coding tools: the code,
tests, and documentation are largely written by
[Claude Code](https://claude.com/claude-code), working from the author's
design brief and reviewed by the author. If that changes how much you
want to rely on this package, that's a fair call — read the source, it's
small.
