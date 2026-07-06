# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the SPARQLGraphStore dispatch/lifecycle (no server)."""

import logging

from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.sparql_graph_store import (
    SPARQLGraphStore,
)
from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.sparql_endpoint_client import (
    SPARQLEndpointClient,
)


class _FakeClient:
    def __init__(self):
        self.updates = []
        self.queries = []
        self.closed = False

    def update(self, sparql):
        self.updates.append(sparql)

    def query(self, sparql):
        self.queries.append(sparql)
        return [{'l': 'stmt-1'}]

    def close(self):
        self.closed = True


def _store(**kwargs):
    return SPARQLGraphStore(query_endpoint='http://ex.test/query', **kwargs)


def test_node_id_formats_id():
    assert _store().node_id('entityId')


def test_client_property_lazily_builds_and_caches_real_client():
    store = _store()
    client = store.client
    assert isinstance(client, SPARQLEndpointClient)
    assert store.client is client  # cached on the private attr


def test_client_property_unwraps_secret_password():
    store = _store(username='u', password='pw')
    assert store.client._auth is not None  # real password reached the client


def test_execute_query_routes_noop_write_and_read():
    store = _store()
    fake = _FakeClient()
    store._client = fake

    # index DDL and CALL procedures are no-ops
    assert store._execute_query('CREATE INDEX FOR (n:`__Entity__`) ON (n.entityId)') == []
    assert store._execute_query('CALL db.indexes()') == []
    assert fake.updates == [] and fake.queries == []

    # build-path writes become a SPARQL update
    store._execute_query(
        "// insert entities\nUNWIND $params AS params\n"
        "MERGE (e:`__Entity__`{entityId: params.e_id})",
        {'params': [{'e_id': 'e1', 'v': 'Alice'}]},
    )
    assert len(fake.updates) == 1 and 'INSERT DATA' in fake.updates[0]

    # reads are dispatched to the read templates
    rows = store._execute_query('// chunk-based graph search',
                                {'chunkId': 'c1', 'statementLimit': 3})
    assert rows == [{'l': 'stmt-1'}] and len(fake.queries) == 1


def test_execute_query_write_with_empty_params_sends_nothing():
    store = _store()
    fake = _FakeClient()
    store._client = fake
    store._execute_query(
        "// insert entities\nUNWIND $params AS params\n"
        "MERGE (e:`__Entity__`{entityId: params.e_id})",
        {'params': []},
    )
    assert fake.updates == []


def test_getstate_drops_client():
    store = _store()
    store._client = _FakeClient()
    state = store.__getstate__()
    assert store._client is None
    assert state is not None


def test_exit_closes_and_clears_client():
    store = _store()
    fake = _FakeClient()
    store._client = fake
    assert store.__exit__(None, None, None) is False
    assert fake.closed is True
    assert store._client is None


def test_execute_query_emits_debug_timing(caplog):
    store = _store()
    store._client = _FakeClient()
    logger_name = 'graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.sparql_graph_store'
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        store._execute_query('CALL db.indexes()')
    assert any('(noop)' in record.getMessage() for record in caplog.records)
