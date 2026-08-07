# Action0-Service

[![CI](https://github.com/LaughInJar/action0-service/actions/workflows/ci.yml/badge.svg)](https://github.com/LaughInJar/action0-service/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/action0-service)](https://pypi.org/project/action0-service/)

A lightweight service registry and dependency injection framework:
register classes, factories, or ready-made instances — by type or by
name — and request them back with their constructor dependencies
resolved automatically. Scopes (singleton, transient, thread-local,
context-local), YAML service catalogs, and a test-friendly override
mechanism included. The core has zero dependencies.

Requires Python 3.11 or newer.

Full documentation including the API reference:
<https://laughinjar.github.io/action0-service/>

## Installation

```shell
pip install action0-service          # or: uv add action0-service
pip install "action0-service[yaml]"  # with YAML catalog support (PyYAML)
```

## Usage

Register services and let the registry wire them together:

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
print(repo.db.dsn)
# postgres://db/app
print(registry.get(UserRepository) is repo)  # singletons by default
# True
```

Services can be named (several implementations of one type), scoped, and
looked up by type — subclass-aware — or by name:

```python
registry.register(Database, name="replica", params={"dsn": "postgres://replica/app"})
registry.get("replica")  # by name
registry.get(Database)  # the unnamed default
registry.get(Database, name="replica")  # typed + named
registry.register(UserRepository, name="fresh", scope=Scope.TRANSIENT, replace=True)
```

Or define whole service catalogs in YAML (anchors and merges work as usual):

```yaml
database:
  factory: myapp.db.Database
  dsn: !ENV ${DATABASE_DSN:-sqlite://}

user-repository:
  factory: myapp.repo.UserRepository
  db: !ref database
```

```python
registry.load_yaml("conf/services.yaml")
registry.validate()  # catch config mistakes at boot, not at first use
```

For tests, swap any service temporarily:

```python
with registry.override(Database, FakeDatabase()):
    ...
```

See the [documentation](https://laughinjar.github.io/action0-service/) for
injection rules, scopes, custom scopes, the full YAML format, parent
registries, lifecycle management (`warmup()` / `close()`), and the
`@registry.inject` function decorator.

The `action0` namespace is simply the one the author likes to use for
personal projects.

## AI disclosure

This library is developed with heavy use of AI coding tools: the code,
tests, and documentation are largely written by
[Claude Code](https://claude.com/claude-code), working from the author's
design brief and reviewed by the author. If that changes how much you want
to rely on this package, that's a fair call — read the source, it's small.

## License

MIT — see [LICENSE](LICENSE).
