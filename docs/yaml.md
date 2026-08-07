# YAML service catalogs

Whole registries can be defined in YAML — wiring as configuration,
without a rebuild. Loading requires PyYAML (install
`action0-service[yaml]`):

```python
registry.load_yaml("conf/services.yaml")
registry.validate()  # catch config mistakes at boot, not at first use
```

{py:meth}`~action0.service.registry.Registry.load_yaml` accepts a file
path or an open text stream, registers every definition in document
order, and returns the created
{py:class}`~action0.service.definitions.Definition` objects. Pass
`replace=True` to overwrite colliding registrations — e.g. to layer a
site-specific file over a default one.

## The format

The document is a mapping of **service names** to definitions. Within a
definition, a handful of *reserved keys* configure the registration —
every other key is a constructor parameter:

```yaml
shop.catalog:
  factory: myapp.catalog.ApiCatalog   # dotted path — required
  scope: singleton                    # optional (default: singleton)
  provides: myapp.catalog.Catalog     # optional, defaults to the class
  default: true                       # optional: wins type lookups
  eager: false                        # optional: built by warmup()
  api_key: !ENV ${CATALOG_KEY}        # everything else: init params
  db: !ref database                   # inject another service by name
  retry_policy:                       # nested mapping with "factory":
    factory: myapp.util.Retry         # built fresh as an anonymous object
    attempts: 3
```

`factory`
: The dotted path of the class or factory callable, required. The
  longest importable module prefix is imported and the rest resolved
  with `getattr`, so nested classes work.

`scope`
: A scope key as a string: `singleton` (the default), `transient`,
  `thread`, `context`, or a [custom scope](scopes.md#custom-scopes)
  registered on the target registry.

`provides`
: Dotted path of the type to register under; defaults to the factory
  class itself (or a plain factory function's return annotation).

`default`, `eager`
: Booleans, both `false` by default — whether the definition wins
  ambiguous type lookups, and whether
  {py:meth}`~action0.service.registry.Registry.warmup` builds it.
  Unlike programmatic registration, YAML services are always *named*
  (their mapping key), so none is a default implementation unless
  marked.

`params`
: An escape hatch: a mapping passed through as constructor parameters
  *without* reserved-key screening — for constructors whose parameter
  is itself called `factory`, `scope`, and so on. Everything under
  `params` behaves exactly like a flat parameter key; declaring the
  same parameter both flat and under `params` is an error.

Constructor parameters that are missing from the YAML follow the normal
[injection rules](injection.md) — annotated dependencies are still
resolved from the registry.

## `!ENV` — environment substitution

The `!ENV` tag substitutes `${VAR}` and `${VAR:-fallback}` inside a
scalar from the process environment, at **load time**. A variable
without fallback that is not set raises
{py:class}`~action0.service.errors.DefinitionError`.

Substitution is purely textual and the result is always a **string** —
it is *not* re-parsed as YAML, so a secret like `yes` or `0123` cannot
change type behind your back. If the constructor wants an `int`,
convert in the constructor or use a factory.

```yaml
database:
  factory: myapp.db.Database
  dsn: !ENV ${DATABASE_DSN:-sqlite://}
```

## `!ref` — service references

`!ref name` injects the service registered under `name`, resolved at
**build time** — the referenced service may be defined later in the
file, in another file, or programmatically. It is the YAML spelling of
{py:class}`~action0.service.markers.Ref` and works nested inside lists
and mappings.

## Anonymous nested factories

A mapping *inside the parameters* that contains a `factory` key becomes
an anonymous, unregistered definition: it is built fresh (transient)
every time the owning service is built, and its own parameters follow
the same rules. Inside nested factories only `factory` and `params` are
reserved. Use them for helper objects that don't deserve a registry
entry of their own — like the `retry_policy` above.

## Anchors, merges, and templates

Standard YAML anchors and merge keys work as usual for sharing
configuration between definitions. Entries whose key starts with a dot
are **templates**: parsed, so their anchors can be referenced — but not
registered:

```yaml
.defaults: &defaults
  factory: myapp.catalog.ApiCatalog
  timeout: 30

catalog.eu:
  <<: *defaults
  api_key: !ENV ${EU_API_KEY}

catalog.us:
  <<: *defaults
  api_key: !ENV ${US_API_KEY}
```

## Security

Parsing uses a {py:class}`yaml.SafeLoader` subclass, so documents
cannot instantiate arbitrary Python objects *during parsing* — but
`factory` paths **are imported and called** when services are built.
Only load files you trust.
