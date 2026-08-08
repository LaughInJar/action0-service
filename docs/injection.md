# Injection

When the registry builds a service, it decides a value for every
parameter of the provider's signature — the `__init__` of a class, or
the factory callable itself. The rules are few and strictly ordered.

## The resolution order

For each parameter, the first applicable rule wins:

1. **Configured `params`.** A value given at registration (or in a
   YAML file) is used as-is —
   {py:class}`~action0.service.markers.Ref` markers and nested
   containers are resolved first, see [below](#ref-values).
2. **A `Named` qualifier.** If the annotation is
   `Annotated[X, Named("some.name")]`, the service registered under
   that name is injected (see [below](#the-named-qualifier)).
3. **The annotated type.** If the annotation is a class — after
   unwrapping `Annotated[...]` layers and `X | None` — the registry
   resolves it like `get(X)` would, including subclass-awareness,
   default selection, and parent fallback. A runtime-checkable
   {py:class}`typing.Protocol` annotation resolves structurally, see
   [structural lookups](lookup.md#structural-lookups-protocols).
   Value-ish builtins are exempt, see
   [below](#what-is-never-injected).
4. **The provider's own default.** If nothing was configured and no
   service matches, a declared default value stands.
5. **`None` for optional annotations.** An `X | None` parameter
   without a default becomes `None` when `X` cannot be resolved.
6. Otherwise the parameter is unresolvable and
   {py:class}`~action0.service.errors.InjectionError` is raised, naming
   the parameter and the missing type.

In short: explicit configuration beats registry wiring beats declared
defaults — and a parameter that *can* fall back never raises.

```python
from action0.service import Registry


class Database:
    def __init__(self, dsn: str = "sqlite://"):
        self.dsn = dsn


class Cache:
    pass


class Repository:
    def __init__(self, db: Database, cache: Cache | None = None, timeout: float = 5.0):
        self.db = db  # rule 3: resolved from the registry
        self.cache = cache  # rules 3-5: injected if registered, else None
        self.timeout = timeout  # rule 4: no Service for float — default stands


registry = Registry()
registry.register(Database)
registry.register(Repository)
repository = registry.get(Repository)
```

## What is never injected

Bare annotations of value-ish builtin types — `str`, `int`, `float`,
`bool`, `bytes`, `list`, `dict`, `set`, `tuple`, and friends — are
**never** resolved from the registry: injecting "the registered `str`"
into every `host: str` parameter would be a footgun. Such parameters
are filled from `params`, their defaults, or an explicit
`Annotated[str, Named("...")]` qualifier, which bypasses the exemption
on purpose.

Multi-type unions (`A | B`) are not injectable by type either — the
registry will not guess which side you meant. `X | None` is the one
union form it understands: it means *optional `X`*.

Parameters without any annotation follow the same path as unresolvable
types: configured value, then default, then
{py:class}`~action0.service.errors.InjectionError`.

## The `Named` qualifier

When several services provide one type,
{py:class}`~action0.service.markers.Named` picks one by name, inside
{py:data}`typing.Annotated`:

```python
from typing import Annotated

from action0.service import Named


class Sync:
    def __init__(self, source: Database, target: Annotated[Database, Named("replica")]):
        self.source = source
        self.target = target
```

If the named service is missing, an optional annotation yields `None`,
a declared default stands, and otherwise
{py:class}`~action0.service.errors.InjectionError` is raised.

## `Ref` values

Inside `params`, a {py:class}`~action0.service.markers.Ref` is a
late-bound reference to another service — by name or by type — resolved
when the depending service is built. Lists, tuples, and dicts in
`params` are walked recursively, so refs can sit inside containers:

```python
from action0.service import Ref

registry.register(
    Sync,
    name="nightly",
    params={"target": Ref("replica"), "source": Ref(Database)},
)
```

Use a `Ref` when the *registration* should decide the wiring; use
`Named` when the *class* should declare it.

## Signature details

- **Positional-only parameters** are filled positionally. If an
  earlier positional-only parameter fell back to its default, a later
  one cannot be filled anymore — that raises
  {py:class}`~action0.service.errors.DefinitionError` when building.
- **`*args`** is ignored; **`**kwargs`** makes the provider accept
  configured `params` keys beyond its named parameters. Unknown
  `params` keys on a provider *without* `**kwargs` raise
  {py:class}`~action0.service.errors.DefinitionError`.
- **Uninspectable providers** (some C-implemented callables expose no
  signature) get their configured `params` passed verbatim as keyword
  arguments; injection by annotation is unavailable for them.
- **Unresolvable type hints** (dangling forward references and the
  like) disable annotation-based injection for that provider; `params`
  and defaults still work.

## Injecting into functions: `@registry.inject`

{py:meth}`~action0.service.registry.Registry.inject` extends injection
to ordinary functions. Only parameters whose default is the
{py:data}`~action0.service.markers.injected` sentinel take part — the
signature stays honest for callers and type checkers:

```python
from action0.service import injected


class Mailer:
    def send(self, subject: str) -> None:
        pass


registry.register(Mailer)


@registry.inject
def send_report(report: str, mailer: Mailer = injected) -> None:
    mailer.send(report)


send_report("weekly")  # mailer resolved from the registry
send_report("weekly", mailer=Mailer())  # explicit argument wins
```

Resolution happens **per call**, against the registry the decorator
came from — an active [override](registries.md#overrides-for-tests)
is honored. Passing `injected` explicitly also triggers resolution
(useful when the parameter sits before others you want to pass). A
sentinel parameter that cannot be resolved raises
{py:class}`~action0.service.errors.InjectionError` at call time.
