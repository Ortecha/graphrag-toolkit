# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from graphrag_toolkit.lexical_graph.versioning import (
    VALID_FROM,
    VALID_TO,
    EXTRACT_TIMESTAMP,
    BUILD_TIMESTAMP,
    VERSION_INDEPENDENT_ID_FIELDS,
)
from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.ontology import (
    LEXICAL_SCHEMA,
    NamespaceConfig,
)
from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.sparql_templates import execute_read


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
        if 'SELECT ?content' in sparql:
            return [{'content': 'Chunk text'}]
        if 'SELECT ?statement ?details' in sparql:
            return [{'statement': 'Alice manages Bob', 'details': 'detail'}]
        if 'SELECT ?entityId ?value ?class ?otherId' in sparql:
            return [{
                'entityId': 'entity-1',
                'value': 'Alice',
                'class': 'Person',
                'otherId': 'entity-2',
                'score': 3,
            }]
        if 'SELECT ?entityId ?value ?class' in sparql:
            return [{
                'entityId': 'entity-1',
                'value': 'Alice',
                'class': 'Person',
                'score': 2,
            }]
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


def test_get_chunk_content_returns_retriever_shape():
    client = FakeClient()
    rows = execute_read(
        client,
        '// get chunk content',
        {'nodeIds': ['chunk-1']},
    )

    assert rows == [{'content': 'Chunk text'}]
    assert 'VALUES ?chunkId { "chunk-1" }' in client.queries[0]


def test_chunk_based_graph_search_returns_statement_ids():
    client = FakeClient()
    rows = execute_read(
        client,
        '// chunk-based graph search',
        {'chunkId': 'chunk-1', 'statementLimit': 3},
    )

    assert rows == [{'l': 'stmt-1'}]
    assert 'lg:statementMentionedIn ?chunk' in client.queries[0]


def test_chunk_based_entity_network_search_uses_node_id_parameter():
    client = FakeClient()
    rows = execute_read(
        client,
        '// chunk-based entity network search',
        {'nodeId': 'chunk-1', 'statementLimit': 3},
    )

    assert rows == [{'l': 'stmt-1'}]
    assert 'VALUES ?chunkId' not in client.queries[0]
    assert 'lg:id "chunk-1"' in client.queries[0]


def test_topic_content_returns_statement_details():
    client = FakeClient()
    rows = execute_read(
        client,
        '// get topic content',
        {'topicId': 'topic-1', 'statementLimit': 3},
    )

    assert rows == [{'statement': 'Alice manages Bob', 'details': 'detail'}]
    assert 'lg:belongsTo ?topic' in client.queries[0]


def test_entities_for_keywords_scores_fact_links():
    client = FakeClient()
    rows = execute_read(
        client,
        '// get entities for keywords\nWHERE entity.search_str = $keyword',
        {'keyword': 'alice'},
    )

    assert rows == [{
        'result': {
            'entity': {'entityId': 'entity-1', 'value': 'Alice', 'class': 'Person'},
            'score': 2.0,
        },
    }]
    assert 'FILTER(?searchStr = "alice")' in client.queries[0]
    assert 'VALUES ?factPredicate { lg:subject lg:object }' in client.queries[0]


def test_entities_for_keywords_supports_starts_with_classification():
    client = FakeClient()
    execute_read(
        client,
        '// get entities for keywords\nWHERE entity.search_str STARTS WITH $keyword and entity.class STARTS WITH $classification',
        {'keyword': 'ali', 'classification': 'Per'},
    )

    assert 'FILTER(STRSTARTS(?searchStr, "ali"))' in client.queries[0]
    assert 'FILTER(STRSTARTS(?class, "Per"))' in client.queries[0]


def test_entities_for_chunk_ids_scores_entities_in_matching_chunks():
    client = FakeClient()
    rows = execute_read(
        client,
        '// get entities for chunk ids',
        {'nodeIds': ['chunk-1'], 'limit': 5},
    )

    assert rows[0]['result']['entity']['entityId'] == 'entity-1'
    assert 'VALUES ?chunkId { "chunk-1" }' in client.queries[0]
    assert 'lg:statementMentionedIn ?chunk' in client.queries[0]


def test_next_level_in_tree_returns_entity_and_neighbours():
    client = FakeClient()
    rows = execute_read(
        client,
        '// get next level in tree',
        {
            'entityIds': ['entity-1'],
            'excludeEntityIds': ['entity-1'],
            'numNeighbours': 2,
        },
    )

    assert rows == [{
        'result': {
            'entity': {'entityId': 'entity-1', 'value': 'Alice', 'class': 'Person'},
            'others': ['entity-2'],
        },
    }]
    assert 'lg:relSubject ?entity' in client.queries[0]
    assert 'lg:relObject ?other' in client.queries[0]
    assert 'FILTER(?otherId NOT IN ("entity-1"))' in client.queries[0]


def test_expand_entities_scores_requested_ids():
    client = FakeClient()
    rows = execute_read(
        client,
        '// expand entities: score entities by number of relations',
        {'entityIds': ['entity-1']},
    )

    assert rows[0]['result']['score'] == 2.0
    assert 'VALUES ?entityId { "entity-1" }' in client.queries[0]


def test_read_templates_use_custom_prefix_and_namespace():
    namespace = NamespaceConfig(
        prefix='gt',
        schema_namespace='https://example.test/schema#',
        instance_namespace='https://example.test/data/',
        extra_prefixes={'xsd': 'http://www.w3.org/2001/XMLSchema#'},
    )
    client = FakeClient()

    execute_read(
        client,
        '// multiple entity-based graph search',
        {'startId': 'alice', 'endIds': ['bob'], 'statementLimit': 3},
        namespace=namespace,
    )

    assert 'PREFIX gt: <https://example.test/schema#>' in client.queries[0]
    assert 'PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>' in client.queries[0]
    assert 'gt:supportedByFact ?fact' in client.queries[0]
