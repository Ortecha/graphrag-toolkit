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

from .ontology import LEXICAL_SCHEMA, sparql_literal

_PREFIX = f'PREFIX lg: <{LEXICAL_SCHEMA}>'

# Mirrors graphrag_toolkit ...indexing.constants.LOCAL_ENTITY_CLASSIFICATION.
LOCAL_ENTITY_CLASSIFICATION = '__Local_Entity__'


class UnsupportedReadError(NotImplementedError):
    """Raised for retriever queries whose SPARQL template is not yet written."""


def execute_read(client, cypher: str, parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Dispatch a read query to its SPARQL template and reshape the results."""
    marker = _marker(cypher) or ''

    if marker.startswith('get facts for statements'):
        return _facts_for_statements(client, parameters)
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


def _marker(cypher: str):
    for line in cypher.splitlines():
        stripped = line.strip()
        if stripped.startswith('//query_ref'):
            continue
        if stripped.startswith('//'):
            return stripped[2:].strip().lower()
    return None
