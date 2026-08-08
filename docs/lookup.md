# Looking services up

Services are requested with
{py:meth}`~action0.service.registry.Registry.get` — by type, by name,
or both. {py:meth}`~action0.service.registry.Registry.find` is the
non-raising variant, and
{py:meth}`~action0.service.registry.Registry.get_all` returns every
matching service.

```python
from action0.service import Registry


class Database:
    def __init__(self, dsn: str = "sqlite://"):
        self.dsn = dsn


class Postgres(Database):
    pass


registry = Registry()
registry.register(Postgres, name="pg")

registry.get("pg")  # by name
registry.get(Database)  # by type — subclass-aware
registry.get(Database, name="pg")  # typed and named
```

## Type lookups are subclass-aware

A type request matches every registration whose *provided type* is the
requested type or a subclass of it. Registering `Postgres` therefore
also answers `get(Database)` — the same substitution rule the type
checker applies to your code.

When several registrations match, the registry picks a winner in this
order:

1. If exactly **one** candidate is marked *default*, it wins. Unnamed
   registrations are default implicitly; a named one can opt in with
   `default=True`.
2. Otherwise, if exactly one candidate (among the defaults, if there
   are several) provides the requested type **exactly** — not a
   subclass — it wins.
3. Otherwise the request is ambiguous and
   {py:class}`~action0.service.errors.AmbiguousServiceError` is raised,
   listing the candidates.

Ambiguity is deliberately an error rather than a guess: request the
service by name, or mark exactly one candidate with `default=True`.

## Structural lookups: protocols

A runtime-checkable {py:class}`typing.Protocol` can be requested like
any class. The match is *structural*: every registration whose provided
type satisfies the protocol is a candidate — no inheritance required.
Winner selection and ambiguity work exactly as for nominal lookups, and
protocol annotations on constructor parameters
[inject](injection.md#the-resolution-order) the same way.

```python
from typing import Protocol
from typing import runtime_checkable


@runtime_checkable
class Speaker(Protocol):
    def speak(self) -> str: ...


class Dog:  # no Speaker base class anywhere
    def speak(self) -> str:
        return "woof"


registry = Registry()
registry.register(Dog)
registry.get(Speaker).speak()  # 'woof'
```

A protocol also works as the *registration* type:
`register(Dog, provides=Speaker)` verifies that `Dog` satisfies the
protocol and files the service under it, and
`register_instance(dog, provides=Speaker)` does the same for a
ready-made object.

Two protocol shapes cannot be matched structurally; both are reported
as explicit errors rather than silent misses:

- A protocol **without** {py:func}`typing.runtime_checkable` refuses
  `issubclass` checks entirely — looking one up raises
  {py:class}`~action0.service.errors.ServiceError` telling you to add
  the decorator.
- A protocol with **non-method members** (say `dsn: str`) supports
  `isinstance` but not `issubclass`, so it cannot be matched against
  other registrations — such a lookup raises `ServiceError` too. It can
  still be served: `register_instance(obj, provides=ThatProtocol)`
  verifies the object with `isinstance` (which *does* check data
  members), and that registration answers requests for exactly that
  protocol, or by name.

## Name lookups

`get("name")` returns the service registered under that exact name —
no type involved. The combined form `get(Type, name="name")` resolves
by name first and then verifies the result provides the requested type,
raising {py:class}`~action0.service.errors.ServiceNotFoundError` when
it does not. Use it when you want both the disambiguation of a name and
the type safety of a typed request (it also gives type checkers the
correct return type).

## `find`: absence is not an error

{py:meth}`~action0.service.registry.Registry.find` returns `None`
where `get` would raise
{py:class}`~action0.service.errors.ServiceNotFoundError` — for optional
integrations:

```python
if (metrics := registry.find("metrics")) is not None:
    metrics.increment("boot")
```

Ambiguity still raises: an ambiguous request is a configuration
problem, not an absence.

## `get_all`: every matching service

{py:meth}`~action0.service.registry.Registry.get_all` returns one
instance per matching definition — parent registrations first, then
local ones, in registration order. This is the plugin pattern: register
several handlers under different names, collect them all by their
common base type:

```python
class Exporter:
    pass


class CsvExporter(Exporter):
    pass


class JsonExporter(Exporter):
    pass


plugins = Registry()
plugins.register(CsvExporter, name="csv")
plugins.register(JsonExporter, name="json")
[type(e).__name__ for e in plugins.get_all(Exporter)]
# ['CsvExporter', 'JsonExporter']
```

## Introspection

A registry behaves like a small collection of its own definitions
(parents excluded): `Type in registry` / `"name" in registry` test
whether a lookup would find something, `len(registry)` counts
definitions, iteration and
{py:meth}`~action0.service.registry.Registry.definitions` yield the
{py:class}`~action0.service.definitions.Definition` objects in
registration order.
