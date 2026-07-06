# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional
from urllib.parse import quote

LEXICAL_SCHEMA = 'https://awslabs.github.io/graphrag-toolkit/lexical#'
LEXICAL_BASE = 'https://awslabs.github.io/graphrag-toolkit/lexical/'
LEXICAL_PREFIX = 'lg'

RDF_TYPE = '<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>'

_PREFIX_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]*$')
_UNSAFE_IRI_RE = re.compile(r'[\x00-\x20<>"{}|^`\\]')


@dataclass(frozen=True)
class NamespaceConfig:
    """Namespaces used when rendering lexical-graph RDF and SPARQL."""

    prefix: str = LEXICAL_PREFIX
    schema_namespace: str = LEXICAL_SCHEMA
    instance_namespace: str = LEXICAL_BASE
    extra_prefixes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        schema = _namespace_with_separator(self.schema_namespace)
        instance = _namespace_with_separator(self.instance_namespace, separator='/')

        if not _PREFIX_RE.match(self.prefix):
            raise ValueError(f'Invalid SPARQL prefix name: {self.prefix!r}')
        for prefix, namespace in self.extra_prefixes.items():
            if not _PREFIX_RE.match(prefix):
                raise ValueError(f'Invalid SPARQL prefix name: {prefix!r}')
            if prefix == self.prefix and namespace != schema:
                raise ValueError(
                    f'Extra prefix {prefix!r} conflicts with lexical_schema_namespace'
                )

        for iri in (schema, instance, *self.extra_prefixes.values()):
            if _UNSAFE_IRI_RE.search(iri):
                raise ValueError(f'Invalid namespace IRI (unsafe characters): {iri!r}')

        object.__setattr__(self, 'schema_namespace', schema)
        object.__setattr__(self, 'instance_namespace', instance)

    @property
    def prefix_ref(self) -> str:
        return f'{self.prefix}:'

    def term(self, local_name: str) -> str:
        return f'<{self.schema_namespace}{local_name}>'

    def instance_iri(self, kind: str, id_value) -> str:
        return f'<{self.instance_namespace}{kind}/{quote(str(id_value), safe="")}>'

    def tenant_graph_iri(self, tenant_value) -> Optional[str]:
        if not tenant_value:
            return None
        return f'<{self.instance_namespace}tenant/{quote(str(tenant_value), safe="")}>'

    def sparql_prefixes(self) -> str:
        prefixes = [(self.prefix, self.schema_namespace)]
        prefixes.extend(
            (prefix, namespace)
            for prefix, namespace in sorted(self.extra_prefixes.items())
            if prefix != self.prefix
        )
        return '\n'.join(f'PREFIX {prefix}: <{namespace}>' for prefix, namespace in prefixes)


def _namespace_with_separator(namespace: str, separator: str = '#') -> str:
    if namespace.endswith(('#', '/')):
        return namespace
    return f'{namespace}{separator}'


DEFAULT_NAMESPACE = NamespaceConfig()

ID_KEY_TO_KIND = {
    'sourceId': ('source', 'Source'),
    'chunkId': ('chunk', 'Chunk'),
    'topicId': ('topic', 'Topic'),
    'statementId': ('statement', 'Statement'),
    'factId': ('fact', 'Fact'),
    'entityId': ('entity', 'Entity'),
    'sysClassId': ('sysclass', 'SysClass'),
}

LABEL_TO_ID_KEY = {
    '__Source__': 'sourceId',
    '__Chunk__': 'chunkId',
    '__Topic__': 'topicId',
    '__Statement__': 'statementId',
    '__Fact__': 'factId',
    '__Entity__': 'entityId',
    '__SYS_Class__': 'sysClassId',
}

EDGE_TO_PREDICATE = {
    '__EXTRACTED_FROM__': 'extractedFrom',
    '__PARENT__': 'parent',
    '__CHILD__': 'child',
    '__NEXT__': 'next',
    '__BELONGS_TO__': 'belongsTo',
    '__SUPPORTS__': 'supports',
    '__SUBJECT__': 'subject',
    '__OBJECT__': 'object',
}

_SPECIALISED_EDGE = {
    '__MENTIONED_IN__': {'statementId': 'statementMentionedIn', 'topicId': 'topicMentionedIn'},
    '__PREVIOUS__': {'chunkId': 'chunkPrevious', 'statementId': 'statementPrevious'},
}


def edge_predicate(rel_label, subject_id_key):
    """Resolve an LPG edge type to its lexical predicate local name."""
    specialised = _SPECIALISED_EDGE.get(rel_label)
    if specialised:
        return specialised[subject_id_key]
    return EDGE_TO_PREDICATE[rel_label]


def term(local_name, namespace: Optional[NamespaceConfig] = None):
    """Return a schema IRI in angle-bracket form."""
    return (namespace or DEFAULT_NAMESPACE).term(local_name)


def instance_iri(kind, id_value, namespace: Optional[NamespaceConfig] = None):
    """Return a deterministic instance IRI for a node of the given kind.

    The id is percent-encoded so values such as ``aws::abc:def`` are legal IRIs.
    """
    return (namespace or DEFAULT_NAMESPACE).instance_iri(kind, id_value)


def relation_iri(subject_id, predicate, object_id, namespace: Optional[NamespaceConfig] = None):
    """Deterministic IRI for an entity-entity relation node (edge metadata)."""
    digest = hashlib.md5(f'{subject_id}|{predicate}|{object_id}'.encode('utf-8'), usedforsecurity=False).hexdigest()
    return instance_iri('rel', digest, namespace)


def sys_relation_iri(subject_class_id,
                     predicate,
                     object_class_id,
                     namespace: Optional[NamespaceConfig] = None):
    """Deterministic IRI for a sys-class relation node (edge metadata)."""
    digest = hashlib.md5(
        f'{subject_class_id}|{predicate}|{object_class_id}'.encode('utf-8'),
        usedforsecurity=False,
    ).hexdigest()
    return instance_iri('sysrel', digest, namespace)


def tenant_graph_iri(tenant_value, namespace: Optional[NamespaceConfig] = None):
    """Named-graph IRI for a tenant, or ``None`` for the default tenant."""
    return (namespace or DEFAULT_NAMESPACE).tenant_graph_iri(tenant_value)


def strip_tenant(label):
    """Split a possibly tenant-suffixed label.

    ``__Entity__`` -> ``('__Entity__', None)``
    ``__Entity__acme__`` -> ``('__Entity__', 'acme')``
    """
    if label in LABEL_TO_ID_KEY:
        return label, None
    if label.endswith('__'):
        for base in LABEL_TO_ID_KEY:
            if label.startswith(base) and len(label) > len(base):
                tenant = label[len(base):-2]
                if tenant:
                    return base, tenant
    return label, None


def sparql_literal(value):
    """Render a Python value as a SPARQL literal, or ``None`` to skip it.

    Numbers stay numeric (so counters can be incremented with ``BIND``); bools
    map to ``true``/``false``; everything else becomes an escaped string literal.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    text = str(value)
    text = (text.replace('\\', '\\\\')
                .replace('"', '\\"')
                .replace('\n', '\\n')
                .replace('\r', '\\r')
                .replace('\t', '\\t'))
    return f'"{text}"'
