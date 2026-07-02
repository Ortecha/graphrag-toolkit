# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Read-path SPARQL templates for the lexical-graph retrievers.

Each toolkit retriever/provider issues a small, known ``MATCH ... RETURN``
query. This module maps each one to a hand-authored SPARQL query and reshapes
the rows into the exact ``List[dict]`` shape the calling retriever expects
(mirroring Neo4j's ``record.data()``).

Status: the write path is implemented and verified first (per the agreed
milestone). Read templates are the next milestone; until a given query is
implemented its handler raises ``UnsupportedReadError`` with the marker, so the
system fails loudly rather than returning silently-wrong results.

Implemented so far:
* ``// get facts for statements`` – demonstrates the flat-query + Python-reshape
  pattern the remaining templates will follow.
"""

import re
from typing import Any, Dict, List

from graphrag_toolkit.lexical_graph.versioning import (
    VALID_FROM,
    VALID_TO,
    EXTRACT_TIMESTAMP,
    BUILD_TIMESTAMP,
    VERSION_INDEPENDENT_ID_FIELDS,
    TIMESTAMP_LOWER_BOUND,
    TIMESTAMP_UPPER_BOUND,
)

from .ontology import LEXICAL_SCHEMA, sparql_literal

_PREFIX = f'PREFIX lg: <{LEXICAL_SCHEMA}>'

# Mirrors graphrag_toolkit ...indexing.constants.LOCAL_ENTITY_CLASSIFICATION.
LOCAL_ENTITY_CLASSIFICATION = '__Local_Entity__'


class UnsupportedReadError(NotImplementedError):
    """Raised for retriever queries whose SPARQL template is not yet written."""


def execute_read(client, cypher: str, parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Dispatch a read query to its SPARQL template and reshape the results."""
    marker = _marker(cypher) or ''

    if marker.startswith('get statements grouped by topic and source'):
        return _statements_grouped_by_topic_and_source(client, cypher, parameters)
    if marker.startswith('get facts for statements'):
        return _facts_for_statements(client, parameters)
    if marker.startswith('single entity-based graph search'):
        return _single_entity_based_graph_search(client, parameters)
    if marker.startswith('multiple entity-based graph search'):
        return _multiple_entity_based_graph_search(client, parameters)
    if marker.startswith('get complements matching subject'):
        return _complements_matching_subject(client, parameters)
    if marker.startswith('get subjects matching complement') or marker.startswith('get real subjects'):
        return _subjects_matching_complement(client, parameters)

    raise UnsupportedReadError(
        f'Read query not yet supported on the RDFox backend [marker: {marker!r}]. '
        f'Read templates are the next implementation milestone.'
    )


def _param_rows(parameters):
    if 'params' in parameters:
        return parameters['params'] or []
    return [parameters] if parameters else []


def _complements_matching_subject(client, parameters):
    """Local-entity rewrite lookup: real entities that have a local-entity twin
    (same search_str). Empty when local entities are disabled."""
    out = []
    for row in _param_rows(parameters):
        n_id = row.get('nId')
        if not n_id:
            continue
        sparql = f'''{_PREFIX}
SELECT ?n_id ?c_id WHERE {{
  ?n lg:id ?n_id ; lg:search_str ?ss ; lg:class ?ncls .
  FILTER(?n_id = {sparql_literal(n_id)})
  FILTER(?ncls != "{LOCAL_ENTITY_CLASSIFICATION}")
  ?c lg:search_str ?ss ; lg:class "{LOCAL_ENTITY_CLASSIFICATION}" ; lg:id ?c_id .
}}'''
        out.extend(client.query(sparql))
    return out


def _subjects_matching_complement(client, parameters):
    """Local-entity rewrite lookup: pair of nodes by id. Empty unless both
    exist (i.e. a complement that also occurs as a real entity)."""
    out = []
    for row in _param_rows(parameters):
        n_id, c_id = row.get('nId'), row.get('cId')
        if not n_id or not c_id:
            continue
        sparql = f'''{_PREFIX}
SELECT ?n_id ?c_id WHERE {{
  ?n lg:id ?n_id . FILTER(?n_id = {sparql_literal(n_id)})
  ?c lg:id ?c_id . FILTER(?c_id = {sparql_literal(c_id)})
}}'''
        out.extend(client.query(sparql))
    return out


def _single_entity_based_graph_search(client, parameters) -> List[Dict[str, Any]]:
    start_id = parameters.get('startId')
    if not start_id:
        return []
    fact_pattern = f'''
  ?entity lg:id {sparql_literal(start_id)} ;
          lg:subject ?fact .'''
    return _statement_ids_for_fact_pattern(client, fact_pattern, parameters)


def _multiple_entity_based_graph_search(client, parameters) -> List[Dict[str, Any]]:
    start_id = parameters.get('startId')
    end_ids = parameters.get('endIds', []) or []
    if not start_id or not end_ids:
        return []
    end_values = ' '.join(sparql_literal(e) for e in end_ids)
    fact_pattern = f'''
  ?start lg:id {sparql_literal(start_id)} .
  VALUES ?endId {{ {end_values} }}
  ?end lg:id ?endId .
  {{
    {{
      ?rel lg:supportedByFact ?fact .
      {{
        {{ ?rel lg:relSubject ?start ; lg:relObject ?end . }}
        UNION
        {{ ?rel lg:relSubject ?end ; lg:relObject ?start . }}
      }}
    }}
    UNION
    {{
      ?rel1 lg:supportedByFact ?fact .
      {{
        {{ ?rel1 lg:relSubject ?start ; lg:relObject ?mid . }}
        UNION
        {{ ?rel1 lg:relSubject ?mid ; lg:relObject ?start . }}
      }}
      {{
        {{ ?rel2 lg:relSubject ?mid ; lg:relObject ?end . }}
        UNION
        {{ ?rel2 lg:relSubject ?end ; lg:relObject ?mid . }}
      }}
    }}
    UNION
    {{
      {{
        {{ ?rel1 lg:relSubject ?start ; lg:relObject ?mid . }}
        UNION
        {{ ?rel1 lg:relSubject ?mid ; lg:relObject ?start . }}
      }}
      ?rel2 lg:supportedByFact ?fact .
      {{
        {{ ?rel2 lg:relSubject ?mid ; lg:relObject ?end . }}
        UNION
        {{ ?rel2 lg:relSubject ?end ; lg:relObject ?mid . }}
      }}
    }}
  }}'''
    return _statement_ids_for_fact_pattern(client, fact_pattern, parameters)


def _statement_ids_for_fact_pattern(client, fact_pattern: str, parameters) -> List[Dict[str, Any]]:
    limit = int(parameters.get('statementLimit') or 100)
    sparql = f'''{_PREFIX}
SELECT DISTINCT ?l WHERE {{
{fact_pattern}
  ?fact lg:supports ?statement .
  {{
    {{
      ?statement lg:id ?l .
    }}
    UNION
    {{
      ?statement lg:statementPrevious ?previous .
      ?previous lg:id ?l .
    }}
    UNION
    {{
      ?next lg:statementPrevious ?statement ;
            lg:id ?l .
    }}
  }}
}} LIMIT {limit}'''
    return [{'l': row['l']} for row in client.query(sparql) if row.get('l') is not None]


def _facts_for_statements(client, parameters) -> List[Dict[str, Any]]:
    statement_ids = parameters.get('statementIds', []) or []
    if not statement_ids:
        return []
    values = ' '.join(sparql_literal(s) for s in statement_ids)
    sparql = f'''{_PREFIX}
SELECT ?statementId ?factValue WHERE {{
  VALUES ?statementId {{ {values} }}
  ?l lg:id ?statementId .
  ?f lg:supports ?l .
  OPTIONAL {{ ?f lg:value ?factValue }}
}}'''
    rows = client.query(sparql)
    grouped: Dict[str, List[str]] = {}
    for row in rows:
        sid = row['statementId']
        grouped.setdefault(sid, [])
        fact_value = row.get('factValue')
        if fact_value is not None and fact_value not in grouped[sid]:
            grouped[sid].append(fact_value)
    return [{'statementId': sid, 'facts': facts} for sid, facts in grouped.items()]


def _statements_grouped_by_topic_and_source(client, cypher: str, parameters) -> List[Dict[str, Any]]:
    statement_ids = parameters.get('statementIds', []) or []
    if not statement_ids:
        return []

    values = ' '.join(sparql_literal(s) for s in statement_ids)
    sparql = f'''{_PREFIX}
SELECT DISTINCT ?statementId ?statementValue ?details ?chunkId ?topicId ?topicValue ?sourceId WHERE {{
  VALUES ?statementId {{ {values} }}
  ?statement lg:id ?statementId ;
             lg:belongsTo ?topic ;
             lg:statementMentionedIn ?chunk .
  OPTIONAL {{ ?statement lg:value ?statementValue }}
  OPTIONAL {{ ?statement lg:details ?details }}
  ?topic lg:id ?topicId .
  OPTIONAL {{ ?topic lg:value ?topicValue }}
  ?chunk lg:id ?chunkId ;
         lg:extractedFrom ?source .
  ?source lg:id ?sourceId .
}}'''
    rows = client.query(sparql)
    if not rows:
        return []

    source_ids = sorted({row['sourceId'] for row in rows if row.get('sourceId') is not None})
    chunk_ids = sorted({row['chunkId'] for row in rows if row.get('chunkId') is not None})
    source_props = _properties_by_id(client, 'Source', source_ids)
    include_chunk_details = 'metadata: properties(c)' in cypher
    chunk_props = _properties_by_id(client, 'Chunk', chunk_ids) if include_chunk_details else {}

    grouped: Dict[str, Dict[str, Any]] = {}
    topic_indexes: Dict[str, Dict[str, Dict[str, Any]]] = {}
    chunk_seen: Dict[str, Dict[str, set]] = {}
    statement_seen: Dict[str, Dict[str, set]] = {}

    for row in rows:
        source_id = row['sourceId']
        source_metadata = dict(source_props.get(source_id, {}))
        source_metadata.setdefault('sourceId', source_id)

        if source_id not in grouped:
            grouped[source_id] = {
                'score': 0,
                'source': {
                    'sourceId': source_id,
                    'metadata': source_metadata,
                    'versioning': _versioning_from(source_metadata),
                },
                'topics': [],
            }
            topic_indexes[source_id] = {}
            chunk_seen[source_id] = {}
            statement_seen[source_id] = {}

        topic_id = row['topicId']
        topics_by_id = topic_indexes[source_id]
        if topic_id not in topics_by_id:
            topic = {
                'topic': row.get('topicValue') or '',
                'topicId': topic_id,
                'chunks': [],
                'statements': [],
            }
            topics_by_id[topic_id] = topic
            grouped[source_id]['topics'].append(topic)
            chunk_seen[source_id][topic_id] = set()
            statement_seen[source_id][topic_id] = set()

        topic = topics_by_id[topic_id]
        chunk_id = row['chunkId']
        if chunk_id not in chunk_seen[source_id][topic_id]:
            metadata = dict(chunk_props.get(chunk_id, {})) if include_chunk_details else {}
            if include_chunk_details:
                metadata.setdefault('chunkId', chunk_id)
            topic['chunks'].append({
                'chunkId': chunk_id,
                'value': None,
                'metadata': metadata,
            })
            chunk_seen[source_id][topic_id].add(chunk_id)

        statement_id = row['statementId']
        if statement_id not in statement_seen[source_id][topic_id]:
            topic['statements'].append({
                'statementId': statement_id,
                'statement': row.get('statementValue') or '',
                'facts': [],
                'details': row.get('details'),
                'chunkId': chunk_id,
                'score': 0,
            })
            statement_seen[source_id][topic_id].add(statement_id)

    results = []
    for result in grouped.values():
        result['score'] = sum(
            len(topic['statements']) / len(topic['chunks'])
            for topic in result['topics']
            if topic['chunks']
        )
        results.append({'result': result})

    results.sort(key=lambda r: r['result']['score'], reverse=True)
    limit = parameters.get('limit')
    return results[:int(limit)] if limit is not None else results


def _properties_by_id(client, cls: str, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not ids:
        return {}
    values = ' '.join(sparql_literal(i) for i in ids)
    sparql = f'''{_PREFIX}
SELECT ?id ?prop ?value WHERE {{
  VALUES ?id {{ {values} }}
  ?node a lg:{cls} ;
        lg:id ?id ;
        ?prop ?value .
  FILTER(STRSTARTS(STR(?prop), "{LEXICAL_SCHEMA}"))
  FILTER(?prop != lg:id)
  FILTER(isLiteral(?value))
}}'''
    out: Dict[str, Dict[str, Any]] = {i: {} for i in ids}
    for row in client.query(sparql):
        prop = _local_name(row.get('prop'))
        if prop:
            out.setdefault(row['id'], {})[prop] = row.get('value')
    return out


def _versioning_from(metadata: Dict[str, Any]) -> Dict[str, Any]:
    id_fields = metadata.get(VERSION_INDEPENDENT_ID_FIELDS, '')
    return {
        'valid_from': _int_or_default(metadata.get(VALID_FROM), TIMESTAMP_LOWER_BOUND),
        'valid_to': _int_or_default(metadata.get(VALID_TO), TIMESTAMP_UPPER_BOUND),
        'extract_timestamp': _int_or_default(metadata.get(EXTRACT_TIMESTAMP), TIMESTAMP_LOWER_BOUND),
        'build_timestamp': _int_or_default(metadata.get(BUILD_TIMESTAMP), TIMESTAMP_LOWER_BOUND),
        'id_fields': id_fields.split(';') if isinstance(id_fields, str) and id_fields else [],
    }


def _int_or_default(value, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _local_name(uri) -> str:
    if not uri:
        return ''
    return str(uri).rsplit('#', 1)[-1]


def _marker(cypher: str):
    for line in cypher.splitlines():
        stripped = line.strip()
        if stripped.startswith('//query_ref'):
            continue
        if stripped.startswith('//'):
            return stripped[2:].strip().lower()
    return None
