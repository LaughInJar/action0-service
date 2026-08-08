from action0.service.default import default_registry
from action0.service.default import set_default_registry
from action0.service.default import using_default_registry
from action0.service.definitions import Definition
from action0.service.errors import AmbiguousServiceError
from action0.service.errors import CircularDependencyError
from action0.service.errors import DefinitionError
from action0.service.errors import DuplicateServiceError
from action0.service.errors import InjectionError
from action0.service.errors import ScopeError
from action0.service.errors import ServiceError
from action0.service.errors import ServiceNotFoundError
from action0.service.errors import ValidationError
from action0.service.markers import Named
from action0.service.markers import Ref
from action0.service.markers import injected
from action0.service.registry import Registry
from action0.service.scopes import ContextScope
from action0.service.scopes import Scope
from action0.service.scopes import ScopePolicy
from action0.service.scopes import SingletonScope
from action0.service.scopes import ThreadScope
from action0.service.scopes import TransientScope

__version__: str = "0.1.0"

__all__ = [
    "AmbiguousServiceError",
    "CircularDependencyError",
    "ContextScope",
    "Definition",
    "DefinitionError",
    "DuplicateServiceError",
    "InjectionError",
    "Named",
    "Ref",
    "Registry",
    "Scope",
    "ScopeError",
    "ScopePolicy",
    "ServiceError",
    "ServiceNotFoundError",
    "SingletonScope",
    "ThreadScope",
    "TransientScope",
    "ValidationError",
    "default_registry",
    "injected",
    "set_default_registry",
    "using_default_registry",
]
