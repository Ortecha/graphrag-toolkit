# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Live end-to-end test of the RDFox backend against a real RDFox server.

Drives the store through the same ``execute_query_with_retry`` entry point the
graph builders use, replaying representative build-path Cypher, then verifies the
resulting RDF with SPARQL. Uses an isolated temp data store that is dropped on
teardown.

Marked ``integration`` and auto-skips when no RDFox is reachable. Set
``RDFOX_URL`` to connect (default
``rdfox://admin:admin@localhost:12110/graphrag-toolkit-rdf``); see this package's
README. Credentials also honour ``RDFOX_USER`` / ``RDFOX_PASSWORD``.
"""

import os

import pytest

from graphrag_toolkit.lexical_graph.storage import GraphStoreFactory
from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox import (
    RDFoxGraphStoreFactory,
)

pytestmark = pytest.mark.integration

RDFOX_URL = os.environ.get('RDFOX_URL', 'rdfox://admin:admin@localhost:12110/graphrag-toolkit-rdf')
PREFIX = 'PREFIX lg: <https://awslabs.github.io/graphrag-toolkit/lexical#>'


def _p(row):
    return {'params': [row]}


@pytest.fixture(scope='module')
def store():
    GraphStoreFactory.register(RDFoxGraphStoreFactory)
    # Point at an isolated temp data store so the test never touches real data.
    base = RDFOX_URL.rsplit('/', 1)[0]
    test_url = f'{base}/graphrag-rdfox-pytest'
    try:
        s = GraphStoreFactory.for_graph_store(test_url)
        s.client.delete_datastore()
        s.client.create_datastore()
    except Exception as e:  # noqa: BLE001 - any connection failure -> skip
        pytest.skip(f'RDFox not reachable at {test_url}: {e}')
    yield s
    try:
        s.client.delete_datastore()
    except Exception:
        pass


def test_write_and_read_round_trip(store):
    def q(cypher, params):
        store.execute_query_with_retry(cypher, params)

    # entities
    for eid, val in (('alice', 'Alice'), ('bob', 'Bob')):
        q("// insert entities\nUNWIND $params AS params\n"
          "MERGE (entity:`__Entity__`{entityId: params.e_id})\n"
          "ON CREATE SET entity.value = params.v, entity.search_str = params.e_search_str, entity.class = params.ec\n"
          "ON MATCH SET entity.value = params.v, entity.search_str = params.e_search_str, entity.class = params.ec",
          _p({'e_id': eid, 'v': val, 'e_search_str': eid, 'ec': 'Person'}))

    # statement
    q("// insert statements\nUNWIND $params AS params\n"
      "MERGE (statement:`__Statement__`{statementId: params.statement_id})\n"
      "ON CREATE SET statement.value=params.value, statement.details=params.details\n"
      "ON MATCH SET statement.value=params.value, statement.details=params.details",
      _p({'statement_id': 's1', 'value': 'Alice manages Bob', 'details': 'd'}))

    # fact as a reified statement (entity object -> no object literal here)
    fact_cypher = (
        "// insert facts\nUNWIND $params AS params\n"
        "MERGE (statement:`__Statement__`{statementId: params.statement_id})\n"
        "MERGE (fact:`__Fact__`{factId: params.fact_id})\n"
        "ON CREATE SET fact.relation = params.p, fact.value = params.fact\n"
        "ON MATCH SET fact.relation = params.p, fact.value = params.fact\n"
        "MERGE (fact)-[:`__SUPPORTS__`]->(statement)")
    q(fact_cypher, _p({'statement_id': 's1', 'fact_id': 'f1', 'fact': 'Alice manages Bob',
                       'predicate': 'manages', 'object_literal': None}))

    # entity-fact subject/object edges (fact -> entity)
    for role, eid in (('subject', 'alice'), ('object', 'bob')):
        q(f"// insert entity-fact {role} relationship\nUNWIND $params AS params\n"
          "MERGE (fact:`__Fact__`{factId: params.fact_id})\n"
          "MERGE (entity:`__Entity__`{entityId: params.entity_id})\n"
          f"MERGE (entity)-[:`__{role.upper()}__`]->(fact)",
          _p({'fact_id': 'f1', 'entity_id': eid}))

    # -- verify the reified fact (predicate is a shared lg:Relation resource) --
    assert store.client.query(
        f'{PREFIX} ASK {{ ?f a lg:Fact ; lg:predicate ?r ; lg:subject ?s ; lg:object ?o . '
        f'?r a lg:Relation ; lg:value "manages" . ?s lg:id "alice" . ?o lg:id "bob" . }}'
    )[0]['boolean']
    # entity -> entity traversal through the fact (property path)
    assert store.client.query(
        f'{PREFIX} ASK {{ ?a lg:id "alice" . ?b lg:id "bob" . ?a ^lg:subject/lg:object ?b . }}'
    )[0]['boolean']
    assert store.client.query(
        f'{PREFIX} ASK {{ ?e lg:id "alice" ; lg:class "Person" . }}'
    )[0]['boolean']

    # complement object is stored directly as a literal on lg:object
    q(fact_cypher, _p({'statement_id': 's1', 'fact_id': 'f2', 'fact': 'Accuracy CONFIDENCE 95%',
                       'predicate': 'CONFIDENCE', 'object_literal': '95%'}))
    assert store.client.query(f'{PREFIX} ASK {{ ?f lg:id "f2" ; lg:object "95%" . }}')[0]['boolean']

    # -- read template round-trips through _execute_query --
    rows = store.execute_query(
        "// get facts for statements\n"
        "MATCH (f)-[:`__SUPPORTS__`]->(l:`__Statement__`)\n"
        "WHERE l.statementId in $statementIds\n"
        "RETURN l.statementId AS statementId, collect(distinct f.value) AS facts",
        {'statementIds': ['s1']})
    assert rows and rows[0]['statementId'] == 's1'
    assert 'Alice manages Bob' in rows[0]['facts']


def test_graph_summary_counter_increments(store):
    gs = ("// insert graph summary\nUNWIND $params AS params\n"
          "MERGE (sc:`__SYS_Class__`{sysClassId: params.sc_id})\n"
          "ON CREATE SET sc.value = params.sc, sc.count = 1 ON MATCH SET sc.count = sc.count + 1\n"
          "MERGE (oc:`__SYS_Class__`{sysClassId: params.oc_id})\n"
          "ON CREATE SET oc.value = params.oc, oc.count = 1 ON MATCH SET oc.count = oc.count + 1\n"
          "MERGE (sc)-[r:`__SYS_RELATION__`{value: params.p}]->(oc)\n"
          "ON CREATE SET r.count = 1 ON MATCH SET r.count = r.count + 1")
    params = _p({'sc_id': 'sys::P', 'oc_id': 'sys::C', 'sc': 'P', 'oc': 'C', 'p': 'M'})
    store.execute_query_with_retry(gs, params)
    store.execute_query_with_retry(gs, params)

    rows = store.client.query(
        f'{PREFIX} SELECT ?c WHERE {{ ?r a lg:SysRelation ; lg:count ?c }}')
    assert [int(r['c']) for r in rows] == [2]
