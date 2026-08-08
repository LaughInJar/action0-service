# Lifecycle

A registry accompanies the application from boot to shutdown:
`validate()` catches wiring mistakes early, `warmup()` builds the
expensive things up front, `close()` tears down what the registry
created.

```python
from action0.service import Registry

with Registry() as registry:
    # register(...) / load_yaml(...)
    registry.validate()
    registry.warmup()
    # ... run the application ...
    pass
# leaving the block closes the registry
```

## `validate()` — fail at boot, not at first use

{py:meth}`~action0.service.registry.Registry.validate` statically
checks every local definition *without instantiating anything*. It
detects:

- `params` keys that don't exist on the provider,
- required parameters that neither `params`, nor defaults, nor any
  registration can satisfy,
- dangling {py:class}`~action0.service.markers.Ref` targets,
- ambiguous injections (several candidate services, no clear default),
- dependency cycles.

Problems are collected, not raised one at a time: the
{py:class}`~action0.service.errors.ValidationError` lists *all* of
them, one per line (also available as its `problems` attribute). Wire
`registry.validate()` into application startup right after the last
registration — a misconfigured service then fails the deploy instead
of the 3 a.m. request that first touched it.

Validation covers what static analysis can see. Lazily registered
services, providers without inspectable signatures, and unresolvable
type hints are checked as far as possible and otherwise trusted.

## `warmup()` — eager construction

Registrations are lazy by default: nothing is built until first
requested. Services registered with `eager=True` opt into
{py:meth}`~action0.service.registry.Registry.warmup`, which builds them
all (in registration order) and returns the instances. Boot becomes:
validate, then warm up — construction cost and construction *failures*
both move to startup.

```python
registry.register(ConnectionPool, eager=True)
registry.validate()
registry.warmup()  # the pool exists now, or boot has failed loudly
```

## `close()` — orderly teardown

{py:meth}`~action0.service.registry.Registry.close` disposes managed
instances and shuts the registry down:

- Every scope hands over the instances *visible from the calling
  thread and context* — all singletons, the calling thread's
  thread-scoped instances, the current context's context-scoped ones.
  Other threads' instances cannot be reached safely and survive.
- Instances the registry **created** get their `close()` method called,
  if they have one — in reverse creation order, so dependents are
  closed before their dependencies. Objects handed in via
  `register_instance()` are *not* closed: the registry didn't make
  them, it won't break them. Transients were never stored, so they are
  the caller's job too.
- Errors from individual `close()` methods are logged, not raised —
  teardown runs to completion.

Registries are context managers; `with Registry() as registry:` calls
`close()` on exit, as in the example at the top.

After closing, any use of the registry raises
{py:class}`~action0.service.errors.ServiceError`. Closing twice is
fine (the second call is a no-op).

## Async lifecycle

Every lifecycle entry point has an async twin:
{py:meth}`~action0.service.registry.Registry.awarmup`,
{py:meth}`~action0.service.registry.Registry.aclose`, and `async with`
(which calls `aclose()` on exit). Use them whenever the registry holds
[async services](registration.md#async-factories) — the sync methods
refuse async definitions, and sync `close()` cannot await an async
disposer.

```python
async def main() -> None:
    async with Registry() as registry:
        registry.register(make_pool)  # an async factory, eager or not
        registry.validate()
        await registry.awarmup()
        # ... run the application ...
```

Teardown rules match `close()`, with one addition: a managed instance
offering an **`aclose()`** method gets that *awaited*, in preference to
a sync `close()`. The reverse-creation order, the managed-only rule,
and the log-don't-raise error handling are the same.

The sync `close()` skips instances that only have an async `aclose()` —
it cannot await them — and logs a warning naming the service, so a
forgotten `aclose()` shows up in the logs rather than as a silent leak.

One registry with async services is meant to be driven from a single
event loop: concurrent first requests within that loop are
deduplicated, but the async machinery does not coordinate across
loops or with concurrent sync resolution from other threads.
