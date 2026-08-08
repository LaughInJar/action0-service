"""A process-wide default registry, for applications that want one.

Libraries and composable code should pass
:py:class:`~action0.service.registry.Registry` instances around
explicitly; but in an application or script there is often exactly one
registry anyway, and threading it through every call site is ceremony.
:py:func:`default_registry` provides that single instance on demand:

>>> from action0.service import Registry
>>> from action0.service import default_registry
>>> from action0.service import set_default_registry
>>> class Config:
...     def __init__(self, env: str = "prod"):
...         self.env = env
>>> _ = default_registry().register(Config)
>>> default_registry().get(Config).env
'prod'
>>> _ = set_default_registry(None)  # reset so unrelated code starts fresh

The default registry is created lazily on first access and is *never
closed automatically* — closing it (and when) is the application's
responsibility. After closing it, call ``set_default_registry(None)`` so
the next access creates a fresh instance.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from action0.service.registry import Registry

# guards lazy creation and replacement of the process-wide instance
_LOCK = threading.Lock()
_default: Registry | None = None


def default_registry() -> Registry:
    """
    Return the process-wide default registry, creating it on first access.

    :returns: the shared :py:class:`~action0.service.registry.Registry`
        instance (the same one on every call, until it is replaced with
        :py:func:`set_default_registry`)
    """
    global _default
    registry = _default
    if registry is None:
        with _LOCK:
            # double-checked: another thread may have created it while we
            # waited for the lock
            if _default is None:
                _default = Registry()
            registry = _default
    return registry


def set_default_registry(registry: "Registry | None") -> "Registry | None":
    """
    Install ``registry`` as the process-wide default.

    Passing ``None`` resets the default, so the next
    :py:func:`default_registry` call creates a fresh instance — do this
    after closing the previous default.

    The previous instance is returned but not closed; dispose it yourself
    if it held resources.

    :param registry: the new default registry, or ``None`` to reset
    :returns: the previously installed registry, or ``None``
    """
    global _default
    with _LOCK:
        previous = _default
        _default = registry
    return previous


@contextmanager
def using_default_registry(registry: Registry) -> Iterator[Registry]:
    """
    Temporarily install ``registry`` as the process-wide default.

    The previous default (or the not-yet-created state) is restored when
    the ``with`` block ends, even on error — the pattern for tests that
    exercise code relying on :py:func:`default_registry`::

        with Registry() as registry, using_default_registry(registry):
            registry.register_instance(FakeMailer(), provides=Mailer)
            code_under_test()

    Note this swaps process-global state: tests doing this cannot run
    concurrently with other tests that touch the default registry.

    :param registry: the registry to install for the duration
    :returns: a context manager yielding ``registry``
    """
    previous = set_default_registry(registry)
    try:
        yield registry
    finally:
        set_default_registry(previous)
