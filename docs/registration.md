# Registering services

A registration maps a *provider* — a class, a factory callable, or a
ready-made instance — to the *type it provides*, optionally under a
*name*. Everything else (scope, parameters, default flag) is
configuration on top.

## Classes

The common case: register a class, and the registry instantiates it on
first request, [injecting](injection.md) whatever its `__init__` needs.

```python
from action0.service import Registry


class Database:
    def __init__(self, dsn: str = "sqlite://"):
        self.dsn = dsn


registry = Registry()
registry.register(Database)
registry.get(Database)  # a Database, built on first use
```

`register()` accepts keyword-only options; see
{py:meth}`~action0.service.registry.Registry.register` for the full
list:

```python
from action0.service import Scope

registry.register(
    Database,
    name="replica",  # register under a name
    scope=Scope.TRANSIENT,  # a fresh instance per request
    params={"dsn": "postgres://replica/app"},  # constructor parameters
    replace=True,  # overwrite an existing registration
)
```

`params` values are passed to the constructor as-is, with two
exceptions: {py:class}`~action0.service.markers.Ref` markers are
replaced with the referenced service at build time, and lists, tuples,
and dicts are walked recursively so a `Ref` can sit inside a container.

## Factories

Any callable works as a provider. Its return annotation tells the
registry what type it provides:

```python
def make_database() -> Database:
    return Database("factory://")


registry.register(make_database, replace=True)
registry.get(Database)  # built by calling make_database()
```

A factory without a return annotation (a `lambda`, typically) needs an
explicit `provides`:

```python
registry.register(lambda: Database("lambda://"), provides=Database, replace=True)
```

Factory parameters are injected exactly like constructor parameters —
a factory is simply a provider whose signature happens not to be an
`__init__`.

## Ready-made instances

{py:meth}`~action0.service.registry.Registry.register_instance` stores
an object you already built. It is served as-is with singleton
semantics, and because the registry did not create it, `close()` will
not dispose it:

```python
database = Database("instance://")
registry.register_instance(database, name="main")
```

## The decorator form

{py:meth}`~action0.service.registry.Registry.service` is `register()`
as a class (or factory) decorator. It works bare or with arguments, and
returns the decorated object unchanged:

```python
@registry.service
class Clock:
    pass


@registry.service("mailer.bulk", scope=Scope.THREAD)
class BulkMailer:
    pass
```

## Names, defaults, and collisions

Registrations are indexed twice: by the type they provide and — if
named — by name.

- An **unnamed** registration is the *default implementation* for its
  type: it wins ambiguous type lookups. Only one unnamed registration
  per exact type is allowed.
- **Named** registrations let several implementations of one type
  coexist. A name is unique per registry.
- `default=True` marks a *named* registration as the winner of
  ambiguous type lookups; see [lookup](lookup.md) for the exact
  selection rules.

Colliding registrations raise
{py:class}`~action0.service.errors.DuplicateServiceError` unless you
pass `replace=True`, in which case the existing definition is removed
first.

```python
registry.register(Database, name="primary")
registry.register(Database, name="primary", replace=True)  # fine
```

## Registering the provided type explicitly

`provides` registers a provider under a base type. For classes it must
be a superclass; for instances, an `isinstance` check applies:

```python
class Postgres(Database):
    pass


fresh = Registry()
fresh.register(Postgres, provides=Database)
type(fresh.get(Database))  # Postgres
```

This is rarely needed — type lookups are subclass-aware anyway — but it
makes the intent explicit and pins the type used for indexing and
validation.

## One-off construction: `build()`

{py:meth}`~action0.service.registry.Registry.build` constructs an
object with full injection *without* registering or caching it. Explicit
keyword arguments win over injection:

```python
class Report:
    def __init__(self, db: Database, title: str = "untitled"):
        self.db = db
        self.title = title


report = fresh.build(Report, title="weekly")
```

Use it for request handlers, jobs, or other short-lived objects that
want their dependencies wired up without becoming services themselves.

## Plugin discovery

{py:meth}`~action0.service.registry.Registry.load_entry_points` pulls in
services advertised by *other installed packages* via [entry
points](https://packaging.python.org/en/latest/specifications/entry-points/).
A plugin declares its services in its own `pyproject.toml`:

```toml
[project.entry-points."myapp.services"]
s3-storage = "myapp_s3.storage:S3Storage"
extras = "myapp_extras.plugin:setup"
```

and the application collects everything installed into its registry:

```python
registry.load_entry_points("myapp.services")
```

Each entry point is applied with one of two conventions, decided by what
it resolves to:

- a **setup hook** — a plain function with exactly one required
  parameter — is called with the registry and may register any number of
  services itself:

  ```python
  def setup(registry):
      registry.register(S3Storage, name="s3")
      registry.register_instance(load_config(), name="s3-config")
  ```

- anything else (a class or factory callable) is registered under the
  entry point's name, exactly like `register(obj, name="s3-storage")`.

The method returns every definition that was registered — including
those added by setup hooks — and raises
{py:class}`~action0.service.errors.DefinitionError` naming the entry
point and its distribution if one fails to load or register; nothing is
skipped silently. Colliding names follow the usual rules: pass
`replace=True` to overwrite instead of raising.

One caveat: a factory function with exactly one required parameter looks
like a setup hook. Give the parameter a default value, or expose a class
or setup hook instead.
