# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from graphrag_toolkit.lexical_graph.versioning import (
    VALID_FROM,
    VALID_TO,
    EXTRACT_TIMESTAMP,
    BUILD_TIMESTAMP,
    VERSION_INDEPENDENT_ID_FIELDS,
)
from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox.ontology import LEXICAL_SCHEMA
from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox.sparql_templates import execute_read


class FakeClient:
    def __init__(self):
        self.queries = []

    def query(self, sparql):
        self.queries.append(sparql)
        if 'SELECT DISTINCT ?statementId' in sparql:
            return [{
                'statementId': 'stmt-1',
                'statementValue': 'Alice manages Bob',
                'details': 'detail',
                'chunkId': 'chunk-1',
                'topicId': 'topic-1',
                'topicValue': 'People',
                'sourceId': 'source-1',
            }]
        if 'SELECT DISTINCT ?l' in sparql:
            return [{'l': 'stmt-1'}]
        if 'a lg:Source' in sparql:
            return [
                {'id': 'source-1', 'prop': f'{LEXICAL_SCHEMA}title', 'value': 'Source title'},
                {'id': 'source-1', 'prop': f'{LEXICAL_SCHEMA}{VALID_FROM}', 'value': 10},
                {'id': 'source-1', 'prop': f'{LEXICAL_SCHEMA}{VALID_TO}', 'value': 20},
                {'id': 'source-1', 'prop': f'{LEXICAL_SCHEMA}{EXTRACT_TIMESTAMP}', 'value': 11},
                {'id': 'source-1', 'prop': f'{LEXICAL_SCHEMA}{BUILD_TIMESTAMP}', 'value': 12},
                {'id': 'source-1', 'prop': f'{LEXICAL_SCHEMA}{VERSION_INDEPENDENT_ID_FIELDS}', 'value': 'doc;rev'},
            ]
        if 'a lg:Chunk' in sparql:
            return [{'id': 'chunk-1', 'prop': f'{LEXICAL_SCHEMA}value', 'value': 'Chunk text'}]
        return []


def test_statements_grouped_by_topic_and_source_shapes_result():
    client = FakeClient()
    rows = execute_read(
        client,
        '// get statements grouped by topic and source\nmetadata: properties(c)',
        {'statementIds': ['stmt-1'], 'limit': 5},
    )

    result = rows[0]['result']
    assert result['score'] == 1
    assert result['source']['sourceId'] == 'source-1'
    assert result['source']['metadata']['title'] == 'Source title'
    assert result['source']['versioning'] == {
        'valid_from': 10,
        'valid_to': 20,
        'extract_timestamp': 11,
        'build_timestamp': 12,
        'id_fields': ['doc', 'rev'],
    }
    assert result['topics'][0]['chunks'][0]['metadata']['value'] == 'Chunk text'
    assert result['topics'][0]['statements'][0]['statement'] == 'Alice manages Bob'


def test_multiple_entity_search_uses_relation_fact_link():
    client = FakeClient()
    rows = execute_read(
        client,
        '// multiple entity-based graph search',
        {'startId': 'alice', 'endIds': ['bob'], 'statementLimit': 3},
    )

    assert rows == [{'l': 'stmt-1'}]
    assert 'lg:supportedByFact ?fact' in client.queries[0]
