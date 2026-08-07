# Parent registries and overrides

Registries nest. A child registry answers what it can locally and falls
back to its parent for the rest — and local registrations *shadow* the
parent's. That gives you layered wiring: application services in a
long-lived parent, request- or job-specific services in a short-lived
child; or production wiring in the parent and test doubles in a child.

```python
from action0.service import Registry, Scope


class Database:
    def __init__(self, dsn: str = "sqlite://"):
        self.dsn = dsn


class Repository:
    def __init__(self, db: Database):
        self.db = db


app = Registry()
app.register(Database)
app.register(Repository, scope=Scope.TRANSIENT)

request = Registry(parent=app)
request.register_instance(Database("request://"), replace=True)

request.get(Repository).db.dsn  # 'request://' — child wiring wins
app.get(Repository).db.dsn  # 'sqlite://' — parent unaffected
```

## Who builds where

Two rules make layering predictable:

1. **Scope state lives on the owner.** A singleton registered in the
   parent is one instance, shared by every child; a child cannot
   accidentally get its "own copy" of a parent singleton.
2. **Cached instances are built in the owner's context.** When a child
   triggers the first construction of a parent-owned singleton, its
   dependencies resolve against the *parent's* registrations — never
   against the child's. A shared instance must not capture wiring from
   whichever short-lived child happened to request it first.

Non-caching scopes (`transient`, and custom scopes with
`caches = False`) are the mirror image: every request builds a fresh,
unshared instance, so dependencies resolve through the *requesting*
registry and pick up the child's shadowing — as `Repository` does in
the example above.

`get_all()` follows the layering too: parent registrations come first,
then local ones, in registration order.

## Overrides, for tests

{py:meth}`~action0.service.registry.Registry.override` temporarily
replaces a service — by type or by name — for the duration of a `with`
block. While active, the override wins *every* lookup, including
injections into other services being built:

```python
class FakeDatabase(Database):
    pass


registry = Registry()
registry.register(Database)
registry.register(Repository, scope=Scope.TRANSIENT)

with registry.override(Database, FakeDatabase()):
    repository = registry.get(Repository)
    assert isinstance(repository.db, FakeDatabase)
```

Type overrides deliberately skip any `isinstance` check — injecting a
`Mock` is the whole point. Overrides nest; the innermost wins.

Two caveats, both consequences of caching:

- Instances cached *before* the override keep their real dependencies —
  an already-built singleton is not rewritten.
- Conversely, a singleton first built *during* the override keeps the
  replacement after the block ends.

In tests, prefer a fresh registry — or a child registry — per test over
overriding in a long-lived one. And override on the registry that
*owns* the service: parent-owned cached services build against the
parent's world, so an override declared on a child cannot reach them
(shadow with a child registration instead, as in the example at the
top).
