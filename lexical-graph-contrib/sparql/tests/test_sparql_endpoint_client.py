# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.sparql_endpoint_client import (
    FORM_URLENCODED,
    SPARQL_JSON,
    SPARQLEndpointClient,
)


class _Response:
    status_code = 200
    text = ''

    def json(self):
        return {
            'results': {
                'bindings': [{'value': {'type': 'literal', 'value': 'ok'}}],
            },
        }


class _Session:
    def __init__(self):
        self.calls = []

    def post(self, url, data, headers, auth, timeout):
        self.calls.append({
            'url': url,
            'data': data,
            'headers': headers,
            'auth': auth,
            'timeout': timeout,
        })
        return _Response()

    def close(self):
        pass


def test_client_sends_form_encoded_query_and_update_requests():
    session = _Session()
    client = SPARQLEndpointClient(
        'http://example.test/query',
        update_endpoint='http://example.test/update',
        headers={'Authorization': 'Bearer token', 'Content-Type': 'text/plain'},
        timeout=12,
    )
    client._session = session

    client.query('SELECT * WHERE { ?s ?p ?o }')
    client.update('INSERT DATA { <urn:s> <urn:p> <urn:o> }')

    query_call, update_call = session.calls
    assert query_call['url'] == 'http://example.test/query'
    assert query_call['data'] == {'query': 'SELECT * WHERE { ?s ?p ?o }'}
    assert query_call['headers']['Content-Type'] == FORM_URLENCODED
    assert query_call['headers']['Accept'] == SPARQL_JSON
    assert query_call['headers']['Authorization'] == 'Bearer token'
    assert query_call['timeout'] == 12

    assert update_call['url'] == 'http://example.test/update'
    assert update_call['data'] == {'update': 'INSERT DATA { <urn:s> <urn:p> <urn:o> }'}
    assert update_call['headers']['Content-Type'] == FORM_URLENCODED
    assert update_call['headers']['Authorization'] == 'Bearer token'
