# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from graphrag_toolkit.lexical_graph.versioning import (
    VALID_FROM,
    VALID_TO,
    EXTRACT_TIMESTAMP,
    BUILD_TIMESTAMP,
    VERSION_INDEPENDENT_ID_FIELDS,
)
import pytest

from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.ontology import (
    DEFAULT_NAMESPACE,
    LEXICAL_SCHEMA,
    NamespaceConfig,
    sparql_literal,
)
from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.sparql_templates import (
    execute_read,
    UnsupportedReadError,
    _entity_score_rows,
    _int_or_default,
    _local_name,
    _properties_by_id,
)


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
        if 'SELECT ?statementId ?factValue' in sparql:
            return [{'statementId': 's1', 'factValue': 'fa'},
                    {'statementId': 's1', 'factValue': 'fa'},
                    {'statementId': 's1'}]
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


def test_multiple_entity_search_traverses_via_facts():
    client = FakeClient()
    rows = execute_read(
        client,
        '// multiple entity-based graph search',
        {'startId': 'alice', 'endIds': ['bob'], 'statementLimit': 3},
    )

    assert rows == [{'l': 'stmt-1'}]
    q = client.queries[0]
    # entity-entity now hops through the reified Fact, not a Relation node
    assert 'lg:supportedByFact' not in q and 'lg:relSubject' not in q
    assert 'lg:subject' in q and 'lg:object' in q


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
    assert 'lg:subject ?entity' in client.queries[0]
    assert 'lg:object ?other' in client.queries[0]
    assert 'relSubject' not in client.queries[0]
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
    assert 'gt:subject' in client.queries[0] and 'gt:supportedByFact' not in client.queries[0]


def test_injection_defenses_escape_values_and_reject_unsafe_namespaces():
    # string values cannot break out of a SPARQL literal
    assert sparql_literal('a" . } DELETE { ?s ?p ?o } #') == '"a\\" . } DELETE { ?s ?p ?o } #"'
    # unsafe namespace IRIs are rejected before any SPARQL is generated
    with pytest.raises(ValueError):
        NamespaceConfig(schema_namespace='https://x/ns#>\nINSERT DATA { <a> <b> <c> } #')


def test_facts_for_statements_groups_and_dedups():
    rows = execute_read(FakeClient(), '// get facts for statements', {'statementIds': ['s1']})
    assert rows == [{'statementId': 's1', 'facts': ['fa']}]


def test_single_entity_based_graph_search():
    client = FakeClient()
    rows = execute_read(client, '// single entity-based graph search',
                        {'startId': 'e1', 'statementLimit': 3})
    assert rows == [{'l': 'stmt-1'}]
    assert 'lg:subject ?entity' in client.queries[0]


def test_topic_based_entity_network_search():
    client = FakeClient()
    rows = execute_read(client, '// topic-based entity network search',
                        {'nodeId': 't1', 'statementLimit': 3})
    assert rows == [{'l': 'stmt-1'}]
    assert 'lg:belongsTo ?topic' in client.queries[0]


def test_entities_for_topic_ids():
    client = FakeClient()
    rows = execute_read(client, '// get entities for topic ids', {'nodeIds': ['t1'], 'limit': 5})
    assert rows[0]['result']['entity']['entityId'] == 'entity-1'
    assert 'VALUES ?topicId { "t1" }' in client.queries[0]


def test_complements_matching_subject_builds_query_and_skips_blank_rows():
    client = FakeClient()
    execute_read(client, '// get complements matching subject',
                 {'params': [{'nId': 'n1'}, {'nId': None}]})
    assert len(client.queries) == 1 and 'search_str' in client.queries[0]


def test_subjects_matching_complement_via_real_subjects_alias():
    client = FakeClient()
    execute_read(client, '// get real subjects', {'nId': 'n1', 'cId': 'c1'})
    assert 'SELECT ?n_id ?c_id' in client.queries[0]


def test_subjects_matching_complement_skips_blank_rows():
    client = FakeClient()
    execute_read(client, '// get subjects matching complement',
                 {'params': [{'nId': None, 'cId': None}]})
    assert client.queries == []


def test_unsupported_read_raises():
    with pytest.raises(UnsupportedReadError):
        execute_read(FakeClient(), 'MATCH (n) RETURN n', {})


def test_marker_skips_query_ref_line():
    rows = execute_read(FakeClient(), '//query_ref abc\n// get chunk content', {'nodeIds': ['chunk-1']})
    assert rows == [{'content': 'Chunk text'}]


@pytest.mark.parametrize('marker,params', [
    ('// get facts for statements', {'statementIds': []}),
    ('// get chunk content', {'nodeIds': []}),
    ('// chunk-based graph search', {}),
    ('// chunk-based entity network search', {}),
    ('// single entity-based graph search', {}),
    ('// multiple entity-based graph search', {'startId': 'a', 'endIds': []}),
    ('// topic-based entity network search', {}),
    ('// get topic content', {}),
    ('// get entities for keywords', {}),
    ('// get entities for chunk ids', {'nodeIds': []}),
    ('// get entities for topic ids', {'nodeIds': []}),
    ('// get next level in tree', {'entityIds': []}),
    ('// expand entities: score entities by number of relations', {'entityIds': []}),
    ('// get statements grouped by topic and source', {'statementIds': []}),
    ('// get complements matching subject', {'params': []}),
    ('// get subjects matching complement', {'params': []}),
])
def test_empty_params_return_empty(marker, params):
    assert execute_read(FakeClient(), marker, params) == []


def test_statements_grouped_returns_empty_when_query_has_no_rows():
    class _Empty:
        def query(self, sparql):
            return []
    assert execute_read(_Empty(), '// get statements grouped by topic and source',
                        {'statementIds': ['s1']}) == []


def test_entity_score_rows_skips_null_entities():
    assert _entity_score_rows([
        {'entityId': None},
        {'entityId': 'e1', 'value': 'v', 'class': 'c', 'score': 2},
    ]) == [{'result': {'entity': {'entityId': 'e1', 'value': 'v', 'class': 'c'}, 'score': 2.0}}]


def test_next_level_in_tree_skips_null_rows():
    class _NullRows:
        def query(self, sparql):
            return [{'entityId': None, 'otherId': None},
                    {'entityId': 'e1', 'value': 'v', 'class': 'c', 'otherId': 'e2', 'score': 1}]
    rows = execute_read(_NullRows(), '// get next level in tree',
                        {'entityIds': ['e1'], 'numNeighbours': 2})
    assert rows[0]['result']['entity']['entityId'] == 'e1'
    assert rows[0]['result']['others'] == ['e2']


def test_properties_by_id_returns_empty_for_no_ids():
    assert _properties_by_id(FakeClient(), 'Source', [], DEFAULT_NAMESPACE) == {}


def test_int_or_default_handles_none_and_bad_values():
    assert _int_or_default(None, 5) == 5
    assert _int_or_default('nope', 5) == 5
    assert _int_or_default('7', 0) == 7


def test_local_name_handles_empty_and_foreign_uris():
    assert _local_name(None, DEFAULT_NAMESPACE) == ''
    assert _local_name('http://other.test/foo#bar', DEFAULT_NAMESPACE) == 'bar'
