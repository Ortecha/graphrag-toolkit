# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the build-path Cypher -> SPARQL translator (no server)."""

import pytest

from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox.cypher_to_sparql_write import (
    translate_write,
    UnsupportedWriteError,
)
from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox.query_router import (
    classify, WRITE, READ, NOOP,
)


def _p(row):
    return {'params': [row]}


def test_classify():
    assert classify('// insert chunks\nUNWIND $params AS params\nMERGE (c:`__Chunk__`{chunkId: params.chunk_id})') == WRITE
    assert classify('MATCH (n) RETURN n') == READ
    assert classify('CREATE INDEX FOR (n:`__Entity__`) ON (n.entityId)') == NOOP
    assert classify('CALL db.indexes()') == NOOP


def test_source_inlined_id_and_props():
    cypher = ("// insert source\nUNWIND $params AS params\n"
              "MERGE (source:`__Source__`{sourceId: 'aws::abc:def'})\n"
              "ON CREATE SET source.url = params.url ON MATCH SET source.url = params.url")
    sparql = translate_write(cypher, _p({'url': 'https://x/y'}))
    assert 'INSERT DATA' in sparql
    # id is percent-encoded into the IRI and stored as a literal
    assert 'source/aws%3A%3Aabc%3Adef' in sparql
    assert 'lg:id' in sparql or '/lexical#id>' in sparql
    assert 'https://x/y' in sparql
    # mutable prop cleared before insert
    assert 'DELETE WHERE' in sparql


def test_entity_node_props():
    cypher = ("// insert entities\nUNWIND $params AS params\n"
              "MERGE (entity:`__Entity__`{entityId: params.e_id})\n"
              "ON CREATE SET entity.value = params.v, entity.search_str = params.e_search_str, entity.class = params.ec\n"
              "ON MATCH SET entity.value = params.v, entity.search_str = params.e_search_str, entity.class = params.ec")
    sparql = translate_write(cypher, _p({'e_id': 'e1', 'v': 'Alice', 'e_search_str': 'alice', 'ec': 'Person'}))
    assert 'entity/e1' in sparql
    assert '"Alice"' in sparql and '"alice"' in sparql and '"Person"' in sparql


def test_spo_relation_node_and_direct_triple():
    cypher = ("// insert entity SPO relations\nUNWIND $params AS params\n"
              "MERGE (subject:`__Entity__`{entityId: params.s_id})\n"
              "MERGE (object:`__Entity__`{entityId: params.o_id})\n"
              "MERGE (subject)-[r:`__RELATION__`{value: params.p}]->(object)")
    sparql = translate_write(cypher, _p({'s_id': 'a', 'o_id': 'b', 'p': 'manages'}))
    assert 'Relation>' in sparql            # intermediate relation node typed
    assert 'relSubject' in sparql and 'relObject' in sparql
    assert '"manages"' in sparql            # edge metadata preserved
    assert 'related>' in sparql             # direct traversal triple


def test_graph_summary_counter_is_read_modify_write():
    cypher = ("// insert graph summary\nUNWIND $params AS params\n"
              "MERGE (sc:`__SYS_Class__`{sysClassId: params.sc_id})\n"
              "ON CREATE SET sc.value = params.sc, sc.count = 1 ON MATCH SET sc.count = sc.count + 1\n"
              "MERGE (oc:`__SYS_Class__`{sysClassId: params.oc_id})\n"
              "ON CREATE SET oc.value = params.oc, oc.count = 1 ON MATCH SET oc.count = oc.count + 1\n"
              "MERGE (sc)-[r:`__SYS_RELATION__`{value: params.p}]->(oc)\n"
              "ON CREATE SET r.count = 1 ON MATCH SET r.count = r.count + 1")
    sparql = translate_write(cypher, _p({'sc_id': 'sys::Person', 'oc_id': 'sys::Company',
                                         'sc': 'Person', 'oc': 'Company', 'p': 'MANAGES'}))
    assert 'SysRelation>' in sparql
    assert 'COALESCE' in sparql and 'BIND' in sparql       # increment, not overwrite
    assert sparql.count('DELETE') >= 2                      # sc + oc + rel counters


def test_plain_edge():
    cypher = ("// insert statement-topic relationships\nUNWIND $params AS params\n"
              "MERGE (statement:`__Statement__`{statementId: params.statement_id})\n"
              "MERGE (topic:`__Topic__`{topicId: params.topic_id})\n"
              "MERGE (statement)-[:`__BELONGS_TO__`]->(topic)")
    sparql = translate_write(cypher, _p({'statement_id': 's1', 'topic_id': 't1'}))
    assert 'belongsTo' in sparql
    assert 'statement/s1' in sparql and 'topic/t1' in sparql


def test_specialised_edges_avoid_union_domains():
    # mentionedIn is specialised by subject kind (statement vs topic)
    s2c = translate_write(
        "// insert statement-chunk relationships\nUNWIND $params AS params\n"
        "MERGE (statement:`__Statement__`{statementId: params.statement_id})\n"
        "MERGE (chunk:`__Chunk__`{chunkId: params.chunk_id})\n"
        "MERGE (statement)-[:`__MENTIONED_IN__`]->(chunk)",
        _p({'statement_id': 's1', 'chunk_id': 'c1'}))
    assert 'statementMentionedIn>' in s2c and '#mentionedIn>' not in s2c

    # previous is specialised by subject kind (chunk vs statement)
    sprev = translate_write(
        "// insert statement-statement prev relationships\nUNWIND $params AS params\n"
        "MERGE (statement:`__Statement__`{statementId: params.statement_id})\n"
        "MERGE (prev_statement:`__Statement__`{statementId: params.prev_statement_id})\n"
        "MERGE (statement)-[:`__PREVIOUS__`]->(prev_statement)",
        _p({'statement_id': 's2', 'prev_statement_id': 's1'}))
    assert 'statementPrevious>' in sprev

    cprev = translate_write(
        "// insert chunk-chunk previous relationships\nUNWIND $params AS params\n"
        "MERGE (chunk:`__Chunk__`{chunkId: params.chunk_id})\n"
        "MERGE (target:`__Chunk__`{chunkId: params.target_id})\n"
        "MERGE (chunk)-[:`__PREVIOUS__`]->(target)",
        _p({'chunk_id': 'c2', 'target_id': 'c1'}))
    assert 'chunkPrevious>' in cprev


def test_relation_vs_sysrelation_predicates_are_distinct():
    spo = translate_write(
        "// insert entity SPO relations\nUNWIND $params AS params\n"
        "MERGE (subject:`__Entity__`{entityId: params.s_id})\n"
        "MERGE (object:`__Entity__`{entityId: params.o_id})\n"
        "MERGE (subject)-[r:`__RELATION__`{value: params.p}]->(object)",
        _p({'s_id': 'a', 'o_id': 'b', 'p': 'manages'}))
    assert 'relSubject>' in spo and 'relObject>' in spo and 'sysRel' not in spo

    gs = translate_write(
        "// insert graph summary\nUNWIND $params AS params\n"
        "MERGE (sc:`__SYS_Class__`{sysClassId: params.sc_id})\n"
        "ON CREATE SET sc.value = params.sc, sc.count = 1 ON MATCH SET sc.count = sc.count + 1\n"
        "MERGE (oc:`__SYS_Class__`{sysClassId: params.oc_id})\n"
        "ON CREATE SET oc.value = params.oc, oc.count = 1 ON MATCH SET oc.count = oc.count + 1\n"
        "MERGE (sc)-[r:`__SYS_RELATION__`{value: params.p}]->(oc)\n"
        "ON CREATE SET r.count = 1 ON MATCH SET r.count = r.count + 1",
        _p({'sc_id': 'p', 'oc_id': 'c', 'sc': 'P', 'oc': 'C', 'p': 'M'}))
    assert 'sysRelSubject>' in gs and 'sysRelObject>' in gs


def test_empty_params_is_noop():
    cypher = ("// insert entity-fact subject relationship\nUNWIND $params AS params\n"
              "MERGE (fact:`__Fact__`{factId: params.fact_id})\n"
              "MERGE (entity:`__Entity__`{entityId: params.entity_id})\n"
              "MERGE (entity)-[:`__SUBJECT__`]->(fact)")
    assert translate_write(cypher, {'params': []}) is None


def test_domain_label():
    cypher = "MERGE (n1:`__Entity__`{entityId: $entityId}) SET n1 :`Person` // awsqid:x"
    sparql = translate_write(cypher, {'entityId': 'e1'})
    assert 'entity/e1' in sparql
    assert 'Person>' in sparql


def test_tenant_routes_to_named_graph():
    cypher = ("// insert entities\nUNWIND $params AS params\n"
              "MERGE (entity:`__Entity__acme__`{entityId: params.e_id})\n"
              "ON CREATE SET entity.value = params.v ON MATCH SET entity.value = params.v")
    sparql = translate_write(cypher, _p({'e_id': 'e1', 'v': 'Alice'}))
    assert 'GRAPH <https://awslabs.github.io/graphrag-toolkit/lexical/tenant/acme>' in sparql


def test_local_entity_rewrite_unsupported():
    cypher = ("// copy complement relationships to subject\nUNWIND $params AS params\n"
              "MATCH (s)-[r:`__RELATION__`]->(c) MERGE (s)-[:`__RELATION__`{value:r.value}]->(n)")
    with pytest.raises(UnsupportedWriteError):
        translate_write(cypher, _p({'n_id': 'x', 'c_id': 'y'}))
