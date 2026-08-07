# API reference

Everything public is importable straight from `action0.service`:

```python
from action0.service import Registry, Scope, Named, Ref, injected
```

The modules below are where the pieces live; the module paths matter
only when subclassing internals (custom
{py:class}`~action0.service.scopes.ScopePolicy` implementations) or
reading tracebacks.

## `action0.service.registry`

```{eval-rst}
.. automodule:: action0.service.registry
```

## `action0.service.scopes`

```{eval-rst}
.. automodule:: action0.service.scopes
```

## `action0.service.markers`

```{eval-rst}
.. automodule:: action0.service.markers
```

## `action0.service.definitions`

```{eval-rst}
.. automodule:: action0.service.definitions
```

## `action0.service.errors`

```{eval-rst}
.. automodule:: action0.service.errors
```

## `action0.service.loader`

```{eval-rst}
.. automodule:: action0.service.loader
```
