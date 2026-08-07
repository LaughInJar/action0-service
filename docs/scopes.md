# Scopes

A scope decides how long a built instance is kept and who shares it.
Every registration has one; the default is `singleton`. Scopes are
addressed through the {py:class}`~action0.service.scopes.Scope` enum or
its string values — the strings are what YAML catalogs use.

```python
from action0.service import Registry, Scope


class Worker:
    pass


registry = Registry()
registry.register(Worker, scope=Scope.THREAD)
registry.register(Worker, name="fresh", scope="transient")
```

## The built-in scopes

`singleton`
: One instance per registry, shared by everyone. Built once, on first
  request, under a process-wide creation lock — concurrent first
  requests from several threads still produce exactly one instance.

`transient`
: A fresh instance for every request. Nothing is stored, so `close()`
  does not dispose transients — whoever asked for one owns it.

`thread`
: One instance per thread, backed by {py:class}`threading.local`.
  Classic use: database sessions and other objects that must not cross
  threads.

`context`
: One instance per {py:mod}`contextvars` context — which makes it
  **task-local under asyncio**, since every task runs in its own
  context copy. The synchronous analogue of a request scope.

## Scopes and parent registries

Scope state lives on the registry that *owns* the definition: a
parent's singleton is one instance shared by all child registries. For
caching scopes the instance is also *built* in the owner's context, so
a shared instance can never capture a child's registrations or
overrides; only non-caching scopes (transient) resolve their
dependencies through the requesting registry. The details are in
[parent registries](registries.md).

## Custom scopes

A scope is a {py:class}`~action0.service.scopes.ScopePolicy`: one
method that either returns a stored instance or calls `build()`. This
request scope stores instances in an explicitly managed slot:

```python
from collections.abc import Callable
from typing import Any

from action0.service import Definition, ScopePolicy


class RequestScope(ScopePolicy):
    """One instance per request; call begin()/end() around each request."""

    def __init__(self) -> None:
        self._store: dict[Definition, Any] | None = None

    def begin(self) -> None:
        self._store = {}

    def end(self) -> None:
        self._store = None

    def get(self, definition: Definition, build: Callable[[], Any]) -> Any:
        if self._store is None:
            raise RuntimeError("no active request")
        if definition not in self._store:
            self._store[definition] = build()
        return self._store[definition]
```

Register the policy under a key, then use the key like any other scope:

```python
request_scope = RequestScope()
registry.register_scope("request", request_scope)
registry.register(Worker, name="per-request", scope="request")
```

Definitions hash by identity, so they can be used as dictionary keys
directly. If your scope stores instances, override
{py:meth}`~action0.service.scopes.ScopePolicy.drain` to hand them over
for disposal when the registry [closes](lifecycle.md); return them in
reverse creation order so dependents are closed before their
dependencies.

The `caches` class attribute declares whether the scope stores
instances (`True`, the default) or builds fresh ones every time
(`False`, like `transient`). It controls which registry's wiring is
used to build, as described [above](#scopes-and-parent-registries).

`register_scope()` can also *replace* a built-in scope — registering a
different policy under `"singleton"` changes what the default scope
means for that registry. That is occasionally useful in tests and
otherwise best left alone.
