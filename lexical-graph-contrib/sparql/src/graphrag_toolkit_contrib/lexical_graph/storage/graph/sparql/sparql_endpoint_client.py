# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""HTTP client for SPARQL 1.1 query and update endpoints."""

from typing import Any, Dict, List, Optional

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError as e:
    raise ImportError(
        "The 'requests' package is required for the SPARQL backend, "
        "install with 'pip install requests'"
    ) from e

SPARQL_JSON = 'application/sparql-results+json'


class SPARQLEndpointClient:
    """Minimal SPARQL-over-HTTP client.

    The client uses the standard form-encoded protocol:
    ``query=<sparql>`` for SELECT/ASK reads and ``update=<sparql>`` for
    SPARQL Update writes. Stores that use different query and update URLs can
    pass both endpoints explicitly.
    """

    def __init__(self,
                 query_endpoint: str,
                 update_endpoint: Optional[str] = None,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 headers: Optional[Dict[str, str]] = None,
                 timeout: float = 60.0):
        self.query_endpoint = query_endpoint
        self.update_endpoint = update_endpoint or query_endpoint
        self.timeout = timeout
        self.headers = headers or {}
        self._auth = HTTPBasicAuth(username, password) if username is not None else None
        self._session = requests.Session()

    def query(self, sparql: str) -> List[Dict[str, Any]]:
        """Run a SELECT/ASK query and return a list of ``{var: value}`` dicts."""
        response = self._session.post(
            self.query_endpoint,
            data={'query': sparql},
            headers={**self.headers, 'Accept': SPARQL_JSON},
            auth=self._auth,
            timeout=self.timeout,
        )
        self._raise_for_status(response, sparql)
        payload = response.json()
        if 'boolean' in payload:
            return [{'boolean': payload['boolean']}]
        return self._rows_from_bindings(payload)

    def update(self, sparql: str) -> None:
        """Run a SPARQL 1.1 update."""
        response = self._session.post(
            self.update_endpoint,
            data={'update': sparql},
            headers=self.headers,
            auth=self._auth,
            timeout=self.timeout,
        )
        self._raise_for_status(response, sparql)

    def count_triples(self) -> int:
        rows = self.query('SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }')
        return int(rows[0]['n']) if rows else 0

    def close(self) -> None:
        self._session.close()

    @staticmethod
    def _raise_for_status(response, sparql: str) -> None:
        if response.status_code >= 400:
            snippet = sparql if len(sparql) < 800 else sparql[:800] + ' ...'
            raise RuntimeError(
                f'SPARQL endpoint request failed [status: {response.status_code}, '
                f'body: {response.text.strip()[:500]}, query: {snippet}]'
            )

    @classmethod
    def _rows_from_bindings(cls, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        results = payload.get('results', {})
        rows = []
        for binding in results.get('bindings', []):
            row = {}
            for var, cell in binding.items():
                row[var] = cls._coerce(cell)
            rows.append(row)
        return rows

    @staticmethod
    def _coerce(cell: Dict[str, Any]) -> Any:
        value = cell.get('value')
        datatype = cell.get('datatype', '')
        if datatype.endswith('integer') or datatype.endswith('int') or datatype.endswith('long'):
            try:
                return int(value)
            except (TypeError, ValueError):
                return value
        if datatype.endswith('decimal') or datatype.endswith('double') or datatype.endswith('float'):
            try:
                return float(value)
            except (TypeError, ValueError):
                return value
        if datatype.endswith('boolean'):
            return value == 'true'
        return value
