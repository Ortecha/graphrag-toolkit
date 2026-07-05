# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Translate the lexical-graph build-path OpenCypher into SPARQL updates.

The toolkit's graph builders each emit a small, regular Cypher pattern that
begins with a stable marker comment (e.g. ``// insert source``). This module
recognises each marker and emits the equivalent SPARQL 1.1 update against the
RDF model defined in :mod:`ontology`.

Semantics mapped:

* ``MERGE (n:Label{id}) ON CREATE/ON MATCH SET ...`` -> per-property
  ``DELETE WHERE`` (clear old scalar) + ``INSERT DATA`` (type, id, props).
  Inserting an already-present triple is a no-op in RDF, so this is idempotent.
* ``MERGE (a)-[:REL]->(b)`` (no edge props) -> a plain object-property triple.
* ``MERGE (a)-[r:__RELATION__{value:p}]->(b)`` -> an intermediate relation node
  carrying the metadata, plus a direct lexical relation triple for traversal.
* ``ON CREATE SET c=n / ON MATCH SET c=c+n`` counters -> read-modify-write
  ``DELETE/INSERT ... WHERE { OPTIONAL ... BIND(COALESCE(?c,0)+n ...) }``.
"""

import re
from typing import Any, Dict, List, Optional

from .ontology import (
    DEFAULT_NAMESPACE,
    NamespaceConfig,
    RDF_TYPE,
    edge_predicate,
    instance_iri,
    relation_iri,
    sparql_literal,
    strip_tenant,
    sys_relation_iri,
    tenant_graph_iri,
    term,
)

_LABEL_RE = re.compile(r'`(__[A-Za-z0-9_]+__)`')
_SOURCE_ID_RE = re.compile(r"sourceId:\s*'([^']*)'")
_DOMAIN_LABEL_RE = re.compile(r'SET\s+\S+\s+:`([^`]+)`')

_lit = sparql_literal


class UnsupportedWriteError(NotImplementedError):
    """Raised for build patterns the SPARQL backend does not support."""


def translate_write(cypher: str,
                    parameters: Dict[str, Any],
                    namespace: NamespaceConfig = DEFAULT_NAMESPACE) -> Optional[str]:
    """Translate one build-path Cypher statement to a SPARQL update.

    Returns the update string, or ``None`` when there is nothing to do (e.g. an
    ``UNWIND`` over an empty parameter list, which is a no-op in Cypher too).
    """
    graph = tenant_graph_iri(_detect_tenant(cypher), namespace)
    rows = _rows(parameters)
    marker = _marker(cypher)

    if marker is None:
        if _DOMAIN_LABEL_RE.search(cypher):
            return _domain_label(cypher, parameters, graph, namespace)
        raise UnsupportedWriteError(f'Unrecognised write (no marker): {cypher[:200]}')

    if any(marker.startswith(p) for p in ('copy complement', 'delete complement', 'insert prev version')):
        if not rows:
            return None
        raise UnsupportedWriteError(
            'Local-entity rewrites are not supported on the SPARQL backend yet; '
            'run with INCLUDE_LOCAL_ENTITIES=False.'
        )

    if not rows:
        return None

    ops: List[str] = []
    for row in rows:
        if marker.startswith('insert source'):
            ops.append(_source(cypher, row, graph, namespace))
        elif marker.startswith('insert chunks'):
            ops.append(_chunk(row, graph, namespace))
        elif marker.startswith('insert chunk-source'):
            ops.append(_edge(row, graph, namespace, 'chunkId', 'chunk_id', 'sourceId', 'source_id', '__EXTRACTED_FROM__'))
        elif marker.startswith('insert chunk-chunk'):
            rel = '__' + marker.split()[2].upper() + '__'
            ops.append(_edge(row, graph, namespace, 'chunkId', 'chunk_id', 'chunkId', 'target_id', rel))
        elif marker.startswith('insert topics'):
            ops.append(_topic(row, graph, namespace))
        elif marker.startswith('insert statements'):
            ops.append(_statement(row, graph, namespace))
        elif marker.startswith('insert statement-chunk'):
            ops.append(_edge(row, graph, namespace, 'statementId', 'statement_id', 'chunkId', 'chunk_id', '__MENTIONED_IN__'))
        elif marker.startswith('insert statement-topic'):
            ops.append(_edge(row, graph, namespace, 'statementId', 'statement_id', 'topicId', 'topic_id', '__BELONGS_TO__'))
        elif marker.startswith('insert statement-statement'):
            ops.append(_edge(row, graph, namespace, 'statementId', 'statement_id', 'statementId', 'prev_statement_id', '__PREVIOUS__'))
        elif marker.startswith('insert facts'):
            ops.append(_fact(row, graph, namespace))
        elif marker.startswith('insert entity-fact'):
            rel = '__' + marker.split()[2].upper() + '__'
            ops.append(_edge(row, graph, namespace, 'entityId', 'entity_id', 'factId', 'fact_id', rel))
        elif marker.startswith('insert entities'):
            ops.append(_entity(row, graph, namespace))
        elif marker.startswith('insert entity spo'):
            ops.append(_relation(row['s_id'], row['o_id'], row.get('p'), graph, namespace, row.get('fact_id')))
        elif marker.startswith('insert entity spc'):
            ops.append(_relation(row['s_id'], row['c_id'], row.get('p'), graph, namespace, row.get('fact_id')))
        elif marker.startswith('insert graph summary'):
            ops.append(_graph_summary(cypher, row, graph, namespace))
        else:
            raise UnsupportedWriteError(f'Unrecognised write marker: {marker!r}')

    ops = [op for op in ops if op]
    return ' ;\n'.join(ops) if ops else None


def _source(cypher: str, row: Dict[str, Any], graph, namespace: NamespaceConfig) -> str:
    match = _SOURCE_ID_RE.search(cypher)
    if not match:
        raise UnsupportedWriteError('Could not parse sourceId from source insert')
    source_id = match.group(1)
    props = []
    for key, value in row.items():
        lit = _lit(value)
        if lit is not None:
            props.append((term(_safe_local(key), namespace), lit))
    return _node_upsert('sourceId', source_id, 'Source', props, graph, namespace)


def _chunk(row: Dict[str, Any], graph, namespace: NamespaceConfig) -> str:
    props = []
    if row.get('text') is not None:
        props.append((term('value', namespace), _lit(row['text'])))
    for key, value in row.items():
        if key in ('chunk_id', 'text'):
            continue
        lit = _lit(value)
        if lit is not None:
            props.append((term(_safe_local(key), namespace), lit))
    return _node_upsert('chunkId', row['chunk_id'], 'Chunk', props, graph, namespace)


def _topic(row: Dict[str, Any], graph, namespace: NamespaceConfig) -> str:
    ops = [_node_upsert('topicId', row['topic_id'], 'Topic',
                        [(term('value', namespace), _lit(row.get('title')))] if row.get('title') is not None else [],
                        graph, namespace)]
    topic_iri = instance_iri('topic', row['topic_id'], namespace)
    for chunk_ref in row.get('chunk_ids', []) or []:
        chunk_id = chunk_ref['chunk_id'] if isinstance(chunk_ref, dict) else chunk_ref
        chunk_iri = instance_iri('chunk', chunk_id, namespace)
        triples = [
            f'{chunk_iri} {RDF_TYPE} {term("Chunk", namespace)} .',
            f'{chunk_iri} {term("id", namespace)} {_lit(chunk_id)} .',
            f'{topic_iri} {term("topicMentionedIn", namespace)} {chunk_iri} .',
        ]
        ops.append(_insert_data('\n'.join(triples), graph))
    return ' ;\n'.join(ops)


def _statement(row: Dict[str, Any], graph, namespace: NamespaceConfig) -> str:
    props = []
    if row.get('value') is not None:
        props.append((term('value', namespace), _lit(row['value'])))
    if row.get('details') is not None:
        props.append((term('details', namespace), _lit(row['details'])))
    return _node_upsert('statementId', row['statement_id'], 'Statement', props, graph, namespace)


def _fact(row: Dict[str, Any], graph, namespace: NamespaceConfig) -> str:
    props = []
    if row.get('p') is not None:
        props.append((term('relation', namespace), _lit(row['p'])))
    if row.get('fact') is not None:
        props.append((term('value', namespace), _lit(row['fact'])))
    ops = [_node_upsert('factId', row['fact_id'], 'Fact', props, graph, namespace)]
    fact_iri = instance_iri('fact', row['fact_id'], namespace)
    stmt_iri = instance_iri('statement', row['statement_id'], namespace)
    triples = [
        f'{stmt_iri} {RDF_TYPE} {term("Statement", namespace)} .',
        f'{stmt_iri} {term("id", namespace)} {_lit(row["statement_id"])} .',
        f'{fact_iri} {term("supports", namespace)} {stmt_iri} .',
    ]
    ops.append(_insert_data('\n'.join(triples), graph))
    return ' ;\n'.join(ops)


def _entity(row: Dict[str, Any], graph, namespace: NamespaceConfig) -> str:
    props = []
    for key, pred in (('v', 'value'), ('e_search_str', 'search_str'), ('ec', 'class')):
        if row.get(key) is not None:
            props.append((term(pred, namespace), _lit(row[key])))
    return _node_upsert('entityId', row['e_id'], 'Entity', props, graph, namespace)


def _edge(row, graph, namespace: NamespaceConfig, a_key, a_param, b_key, b_param, rel_label) -> Optional[str]:
    if a_param not in row or b_param not in row:
        return None
    a_kind, a_cls = _kind_cls(a_key)
    b_kind, b_cls = _kind_cls(b_key)
    a_iri = instance_iri(a_kind, row[a_param], namespace)
    b_iri = instance_iri(b_kind, row[b_param], namespace)
    predicate = term(edge_predicate(rel_label, a_key), namespace)
    triples = [
        f'{a_iri} {RDF_TYPE} {term(a_cls, namespace)} .',
        f'{a_iri} {term("id", namespace)} {_lit(row[a_param])} .',
        f'{b_iri} {RDF_TYPE} {term(b_cls, namespace)} .',
        f'{b_iri} {term("id", namespace)} {_lit(row[b_param])} .',
        f'{a_iri} {predicate} {b_iri} .',
    ]
    return _insert_data('\n'.join(triples), graph)


def _relation(subject_id, object_id, predicate_value, graph, namespace: NamespaceConfig, fact_id=None) -> str:
    s_iri = instance_iri('entity', subject_id, namespace)
    o_iri = instance_iri('entity', object_id, namespace)
    rel = relation_iri(subject_id, predicate_value, object_id, namespace)
    triples = [
        f'{s_iri} {RDF_TYPE} {term("Entity", namespace)} .',
        f'{s_iri} {term("id", namespace)} {_lit(subject_id)} .',
        f'{o_iri} {RDF_TYPE} {term("Entity", namespace)} .',
        f'{o_iri} {term("id", namespace)} {_lit(object_id)} .',
        f'{rel} {RDF_TYPE} {term("Relation", namespace)} .',
        f'{rel} {term("relSubject", namespace)} {s_iri} .',
        f'{rel} {term("relObject", namespace)} {o_iri} .',
    ]
    if predicate_value is not None:
        triples.append(f'{rel} {term("value", namespace)} {_lit(predicate_value)} .')
    if fact_id is not None:
        fact_iri = instance_iri('fact', fact_id, namespace)
        triples.extend([
            f'{fact_iri} {RDF_TYPE} {term("Fact", namespace)} .',
            f'{fact_iri} {term("id", namespace)} {_lit(fact_id)} .',
            f'{rel} {term("supportedByFact", namespace)} {fact_iri} .',
        ])
    return _insert_data('\n'.join(triples), graph)


def _graph_summary(cypher: str, row: Dict[str, Any], graph, namespace: NamespaceConfig) -> str:
    two_class = 'oc:`' in cypher or '(oc:' in cypher
    delta = 1 if two_class else 2

    sc_iri = instance_iri('sysclass', row['sc_id'], namespace)
    ops = [
        _insert_data('\n'.join([
            f'{sc_iri} {RDF_TYPE} {term("SysClass", namespace)} .',
            f'{sc_iri} {term("id", namespace)} {_lit(row["sc_id"])} .',
            f'{sc_iri} {term("value", namespace)} {_lit(row.get("sc"))} .',
        ]), graph),
        _increment(sc_iri, term('count', namespace), delta, graph),
    ]

    object_class_id = row['oc_id'] if two_class else row['sc_id']
    if two_class:
        oc_iri = instance_iri('sysclass', row['oc_id'], namespace)
        ops.append(_insert_data('\n'.join([
            f'{oc_iri} {RDF_TYPE} {term("SysClass", namespace)} .',
            f'{oc_iri} {term("id", namespace)} {_lit(row["oc_id"])} .',
            f'{oc_iri} {term("value", namespace)} {_lit(row.get("oc"))} .',
        ]), graph))
        ops.append(_increment(oc_iri, term('count', namespace), delta, graph))
    else:
        oc_iri = sc_iri

    sysrel = sys_relation_iri(row['sc_id'], row.get('p'), object_class_id, namespace)
    sysrel_triples = [
        f'{sysrel} {RDF_TYPE} {term("SysRelation", namespace)} .',
        f'{sysrel} {term("sysRelSubject", namespace)} {sc_iri} .',
        f'{sysrel} {term("sysRelObject", namespace)} {oc_iri} .',
    ]
    if row.get('p') is not None:
        sysrel_triples.append(f'{sysrel} {term("value", namespace)} {_lit(row["p"])} .')
    ops.append(_insert_data('\n'.join(sysrel_triples), graph))
    ops.append(_increment(sysrel, term('count', namespace), delta, graph))
    return ' ;\n'.join(ops)


def _domain_label(cypher: str,
                  parameters: Dict[str, Any],
                  graph,
                  namespace: NamespaceConfig) -> Optional[str]:
    match = _DOMAIN_LABEL_RE.search(cypher)
    entity_id = parameters.get('entityId')
    if not match or entity_id is None:
        return None
    entity_iri = instance_iri('entity', entity_id, namespace)
    triples = [
        f'{entity_iri} {RDF_TYPE} {term("Entity", namespace)} .',
        f'{entity_iri} {term("id", namespace)} {_lit(entity_id)} .',
        f'{entity_iri} {RDF_TYPE} {term(_safe_local(match.group(1)), namespace)} .',
    ]
    return _insert_data('\n'.join(triples), graph)


def _node_upsert(id_key, id_value, cls, props, graph, namespace: NamespaceConfig) -> str:
    ops = [_delete_prop(instance_iri(*_kind_cls_iri(id_key, id_value), namespace), pred, graph)
           for pred, _ in props]
    iri = instance_iri(*_kind_cls_iri(id_key, id_value), namespace)
    lines = [f'{iri} {RDF_TYPE} {term(cls, namespace)} .', f'{iri} {term("id", namespace)} {_lit(id_value)} .']
    lines.extend(f'{iri} {pred} {lit} .' for pred, lit in props)
    ops.append(_insert_data('\n'.join(lines), graph))
    return ' ;\n'.join(ops)


def _delete_prop(iri, predicate, graph) -> str:
    return f'DELETE WHERE {{ {_wrap(f"{iri} {predicate} ?o", graph)} }}'


def _insert_data(triples, graph) -> str:
    return f'INSERT DATA {{ {_wrap(triples, graph)} }}'


def _increment(iri, predicate, delta, graph) -> str:
    del_block = _wrap(f'{iri} {predicate} ?c', graph)
    ins_block = _wrap(f'{iri} {predicate} ?newc', graph)
    where = (f'{_wrap(f"OPTIONAL {{ {iri} {predicate} ?c0 }}", graph)} '
             f'BIND(COALESCE(?c0, 0) + {delta} AS ?newc) BIND(?c0 AS ?c)')
    return f'DELETE {{ {del_block} }} INSERT {{ {ins_block} }} WHERE {{ {where} }}'


def _wrap(pattern, graph) -> str:
    if graph:
        return f'GRAPH {graph} {{ {pattern} }}'
    return pattern


def _rows(parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
    if parameters is None:
        return []
    if 'params' in parameters:
        return parameters['params'] or []
    return [parameters] if parameters else []


def _marker(cypher: str) -> Optional[str]:
    for line in cypher.splitlines():
        stripped = line.strip()
        if stripped.startswith('//query_ref'):
            continue
        if stripped.startswith('//'):
            return stripped[2:].strip().lower()
    return None


def _detect_tenant(cypher: str) -> Optional[str]:
    for label in _LABEL_RE.findall(cypher):
        _, tenant = strip_tenant(label)
        if tenant:
            return tenant
    return None


def _kind_cls(id_key):
    from .ontology import ID_KEY_TO_KIND
    return ID_KEY_TO_KIND[id_key]


def _kind_cls_iri(id_key, id_value):
    kind, _ = _kind_cls(id_key)
    return kind, id_value


def _safe_local(key) -> str:
    return ''.join(c if (c.isalnum() or c == '_') else '_' for c in str(key))
