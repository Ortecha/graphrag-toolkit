# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib
import sys

import pytest

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


class _Resp:
    def __init__(self, status=200, payload=None, text=''):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _Sess:
    def __init__(self, resp):
        self.resp = resp
        self.closed = False

    def post(self, url, data, headers, auth, timeout):
        return self.resp

    def close(self):
        self.closed = True


def _client_with(resp):
    client = SPARQLEndpointClient('http://x/q')
    client._session = _Sess(resp)
    return client


def test_query_returns_ask_boolean():
    client = _client_with(_Resp(payload={'boolean': True}))
    assert client.query('ASK { ?s ?p ?o }') == [{'boolean': True}]


def test_query_defaults_update_endpoint_to_query_endpoint():
    client = SPARQLEndpointClient('http://x/q')
    assert client.update_endpoint == 'http://x/q'


def test_raise_for_status_raises_on_http_error():
    client = _client_with(_Resp(status=500, text='kaboom'))
    with pytest.raises(RuntimeError):
        client.query('SELECT * WHERE { ?s ?p ?o }')


def test_close_closes_the_session():
    session = _Sess(_Resp())
    client = SPARQLEndpointClient('http://x/q')
    client._session = session
    client.close()
    assert session.closed is True


@pytest.mark.parametrize('cell,expected', [
    ({'value': '5', 'datatype': 'http://www.w3.org/2001/XMLSchema#integer'}, 5),
    ({'value': 'nope', 'datatype': 'http://www.w3.org/2001/XMLSchema#integer'}, 'nope'),
    ({'value': '1.5', 'datatype': 'http://www.w3.org/2001/XMLSchema#double'}, 1.5),
    ({'value': 'nope', 'datatype': 'http://www.w3.org/2001/XMLSchema#double'}, 'nope'),
    ({'value': 'true', 'datatype': 'http://www.w3.org/2001/XMLSchema#boolean'}, True),
    ({'value': 'plain'}, 'plain'),
])
def test_coerce_datatypes(cell, expected):
    assert SPARQLEndpointClient._coerce(cell) == expected


def test_missing_requests_raises_helpful_import_error(monkeypatch):
    module = 'graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.sparql_endpoint_client'
    monkeypatch.setitem(sys.modules, 'requests', None)
    monkeypatch.delitem(sys.modules, module, raising=False)
    with pytest.raises(ImportError):
        importlib.import_module(module)
    monkeypatch.delitem(sys.modules, module, raising=False)
