# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.sparql_graph_store import (
    SPARQLGraphStore,
)
from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.sparql_graph_store_factory import (
    SPARQLGraphStoreFactory,
)


def test_sparql_factory_creates_generic_endpoint_store():
    store = SPARQLGraphStoreFactory().try_create(
        'sparql+https://alice:secret@example.test/sparql/query',
        update_endpoint='https://example.test/sparql/update',
        lexical_prefix='gt',
        lexical_schema_namespace='https://example.test/schema#',
        lexical_instance_namespace='https://example.test/data/',
        sparql_prefixes={'xsd': 'http://www.w3.org/2001/XMLSchema#'},
    )

    assert isinstance(store, SPARQLGraphStore)
    assert store.query_endpoint == 'https://example.test/sparql/query'
    assert store.update_endpoint == 'https://example.test/sparql/update'
    assert store.username == 'alice'
    assert store.password == 'secret'
    assert store.namespace.prefix_ref == 'gt:'
    assert store.namespace.schema_namespace == 'https://example.test/schema#'
    assert store.namespace.instance_namespace == 'https://example.test/data/'
    assert 'PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>' in store.namespace.sparql_prefixes()


def test_sparql_factory_ignores_non_sparql_urls():
    assert SPARQLGraphStoreFactory().try_create('https://example.test/sparql/query') is None


def test_sparql_factory_accepts_https_scheme():
    store = SPARQLGraphStoreFactory().try_create('sparql+s://example.test/sparql/query')
    assert store.query_endpoint == 'https://example.test/sparql/query'


def test_sparql_factory_consumes_auth_kwargs_when_uri_has_credentials():
    store = SPARQLGraphStoreFactory().try_create(
        'sparql+https://alice:secret@example.test/sparql/query',
        username='bob',
        password='ignored',
    )
    assert store.username == 'alice'
    assert store.password == 'secret'


def test_sparql_factory_preserves_endpoint_query_params():
    store = SPARQLGraphStoreFactory().try_create(
        'sparql+https://example.test/sparql/query?default-graph-uri=http%3A%2F%2Fexample.test%2Fg'
        '&update_endpoint=https%3A%2F%2Fexample.test%2Fsparql%2Fupdate'
    )
    assert store.query_endpoint == (
        'https://example.test/sparql/query?default-graph-uri=http%3A%2F%2Fexample.test%2Fg'
    )
    assert store.update_endpoint == 'https://example.test/sparql/update'
