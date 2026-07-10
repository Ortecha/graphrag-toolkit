# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""RDF ontology for the lexical graph.

Single source of truth for the mapping between the toolkit's Labeled Property
Graph (LPG) model and an equivalent RDF model stored in RDFox.

Design summary (see the approved plan):

* Each LPG node becomes an IRI (deterministic, derived from the node's existing
  id) typed with an ``lg:`` class. The raw id is also stored as an ``lg:id``
  literal so SPARQL reads can return it directly.
* Property-free LPG edges become plain object-property triples.
* The two property-bearing LPG edges (``__RELATION__`` and ``__SYS_RELATION__``)
  become **intermediate relation nodes** (matching the toolkit's existing
  ``__Fact__`` / ``__SYS_Class__`` n-ary style), so edge metadata such as
  ``value`` and ``count`` lives on first-class resources. A parallel direct
  ``lg:related`` triple is also asserted between entities so traversal queries
  stay expressible as SPARQL property paths.
* Multi-tenancy: the default tenant uses the default graph; a named tenant ``t``
  uses the named graph ``.../lexical/tenant/t``.
"""

import hashlib
from urllib.parse import quote

# Namespaces ----------------------------------------------------------------

LEXICAL_SCHEMA = 'https://awslabs.github.io/graphrag-toolkit/lexical#'
LEXICAL_BASE = 'https://awslabs.github.io/graphrag-toolkit/lexical/'

RDF_TYPE = '<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>'

# id-property -> (instance-IRI path segment, rdf:type class local name) --------

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

# property-free LPG relationship -> lg: predicate local name -------------------
#
# Domain-ambiguous edges (the same LPG type used between different node kinds)
# are specialised per subject kind instead of carrying a union domain — see
# edge_predicate().

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

# rel label -> {subject id-key -> specialised predicate}
_SPECIALISED_EDGE = {
    '__MENTIONED_IN__': {'statementId': 'statementMentionedIn', 'topicId': 'topicMentionedIn'},
    '__PREVIOUS__': {'chunkId': 'chunkPrevious', 'statementId': 'statementPrevious'},
}


def edge_predicate(rel_label, subject_id_key):
    """Resolve an LPG edge type to its lg: predicate, specialised by the subject
    node kind for the domain-ambiguous edges (mentionedIn, previous)."""
    specialised = _SPECIALISED_EDGE.get(rel_label)
    if specialised:
        return specialised[subject_id_key]
    return EDGE_TO_PREDICATE[rel_label]


# IRI / literal helpers -------------------------------------------------------

def term(local_name):
    """Return a schema (``lg:``) IRI in angle-bracket form."""
    return f'<{LEXICAL_SCHEMA}{local_name}>'


def instance_iri(kind, id_value):
    """Return a deterministic instance IRI for a node of the given kind.

    The id is percent-encoded so values such as ``aws::abc:def`` are legal IRIs.
    """
    return f'<{LEXICAL_BASE}{kind}/{quote(str(id_value), safe="")}>'


def iri_for_id_key(id_key, id_value):
    """Return the instance IRI for a value of a known id property."""
    kind, _ = ID_KEY_TO_KIND[id_key]
    return instance_iri(kind, id_value)


def class_for_id_key(id_key):
    """Return the schema class IRI for a known id property."""
    _, cls = ID_KEY_TO_KIND[id_key]
    return term(cls)


def relation_iri(predicate_value):
    """IRI for a shared predicate/relation resource, merged by normalised
    (case-insensitive, space-insensitive) predicate value.

    So all facts with predicate "USES"/"uses" reference one lg:Relation node,
    the same way entities are merged by their normalised value.
    """
    key = str(predicate_value).lower().replace(' ', '_')
    return instance_iri('relation', key)


def sys_relation_iri(subject_class_id, predicate, object_class_id):
    """Deterministic IRI for a sys-class relation node (edge metadata)."""
    digest = hashlib.md5(
        f'{subject_class_id}|{predicate}|{object_class_id}'.encode('utf-8')
    ).hexdigest()
    return instance_iri('sysrel', digest)


def tenant_graph_iri(tenant_value):
    """Named-graph IRI for a tenant, or ``None`` for the default tenant."""
    if not tenant_value:
        return None
    return f'<{LEXICAL_BASE}tenant/{quote(str(tenant_value), safe="")}>'


def strip_tenant(label):
    """Split a possibly tenant-suffixed label.

    ``__Entity__`` -> ``('__Entity__', None)``
    ``__Entity__acme__`` -> ``('__Entity__', 'acme')``
    """
    if label in LABEL_TO_ID_KEY:
        return label, None
    # tenant-suffixed form: <base><tenant>__  e.g. __Entity__acme__ where the
    # base label is __Entity__ and the tenant is acme.
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
