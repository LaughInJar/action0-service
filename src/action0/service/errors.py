"""Exception hierarchy for :py:mod:`action0.service`.

Every exception raised by this package derives from :py:class:`ServiceError`,
so ``except ServiceError`` catches anything the framework can throw.
"""


class ServiceError(Exception):
    """Base class for all errors raised by :py:mod:`action0.service`."""


class DefinitionError(ServiceError):
    """A service definition is invalid.

    Raised when a registration cannot be accepted: the provider is not
    callable, ``provides`` does not match the provider, a YAML definition
    is malformed, or configured parameters do not exist on the provider.
    """


class DuplicateServiceError(DefinitionError):
    """A registration collides with an existing one.

    Either a service with the same name already exists, or an unnamed
    (default) service for the same ``provides`` type is already registered.
    Pass ``replace=True`` to the registration call to overwrite instead.
    """


class ServiceNotFoundError(ServiceError):
    """No registered service matches the requested type or name."""


class AmbiguousServiceError(ServiceError):
    """More than one registered service matches a type request.

    Disambiguate by requesting the service by name, or mark exactly one of
    the candidates as the default implementation (``default=True``).
    """


class InjectionError(ServiceError):
    """A constructor or function parameter could not be resolved.

    Raised when a required parameter has no configured value, no usable
    default, and no registered service matching its type annotation.
    """


class CircularDependencyError(ServiceError):
    """Two or more services depend on each other, directly or indirectly."""


class ScopeError(ServiceError):
    """A definition references a scope that is not registered."""


class ValidationError(ServiceError):
    """:py:meth:`action0.service.registry.Registry.validate` found problems.

    The message lists every problem found, one per line.
    """

    def __init__(self, problems: list[str]) -> None:
        """
        Collect the problems into a readable multi-line message.

        :param problems: one human-readable description per problem found
        """
        self.problems = problems
        super().__init__("\n".join(f"- {problem}" for problem in problems))
