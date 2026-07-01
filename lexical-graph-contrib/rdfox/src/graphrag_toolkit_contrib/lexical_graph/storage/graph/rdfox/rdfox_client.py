# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Thin HTTP client for an RDFox server's REST/SPARQL endpoint.

RDFox exposes a SPARQL-over-HTTP endpoint at
``/datastores/<datastore>/sparql`` (GET/POST for queries, POST for
DELETE/INSERT updates) and a data-store management API under ``/datastores``.
See https://docs.oxfordsemantic.tech/ .
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError as e:
    raise ImportError(
        "The 'requests' package is required for the RDFox backend, "
        "install with 'pip install requests'"
    ) from e

SPARQL_JSON = 'application/sparql-results+json'


class RDFoxClient:
    """Minimal RDFox REST client (SPARQL query, SPARQL update, admin helpers)."""

    def __init__(self,
                 base_url: str,
                 datastore: str,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 timeout: float = 60.0):
        self.base_url = base_url.rstrip('/')
        self.datastore = datastore
        self.timeout = timeout
        self._auth = HTTPBasicAuth(username, password) if username is not None else None
        self._session = requests.Session()

    # -- endpoints ----------------------------------------------------------

    @property
    def _sparql_endpoint(self) -> str:
        return f'{self.base_url}/datastores/{self.datastore}/sparql'

    # -- queries / updates --------------------------------------------------

    def query(self, sparql: str) -> List[Dict[str, Any]]:
        """Run a SELECT/ASK query and return a list of ``{var: value}`` dicts."""
        response = self._session.post(
            self._sparql_endpoint,
            data={'query': sparql},
            headers={'Accept': SPARQL_JSON},
            auth=self._auth,
            timeout=self.timeout,
        )
        self._raise_for_status(response, sparql)
        payload = response.json()
        if 'boolean' in payload:  # ASK
            return [{'boolean': payload['boolean']}]
        return self._rows_from_bindings(payload)

    def update(self, sparql: str) -> None:
        """Run a SPARQL 1.1 update (sequence of INSERT/DELETE operations)."""
        response = self._session.post(
            self._sparql_endpoint,
            data={'update': sparql},
            auth=self._auth,
            timeout=self.timeout,
        )
        self._raise_for_status(response, sparql)

    # -- admin helpers ------------------------------------------------------

    def datastore_exists(self) -> bool:
        response = self._session.get(
            f'{self.base_url}/datastores',
            auth=self._auth,
            timeout=self.timeout,
        )
        self._raise_for_status(response, '<list datastores>')
        # RDFox returns the datastore name as a quoted token in a TSV table.
        return f'"{self.datastore}"' in response.text

    def create_datastore(self) -> None:
        response = self._session.post(
            f'{self.base_url}/datastores/{self.datastore}',
            auth=self._auth,
            timeout=self.timeout,
        )
        # 201 created, or already-exists is fine.
        if response.status_code not in (200, 201, 409):
            self._raise_for_status(response, '<create datastore>')

    def delete_datastore(self) -> None:
        response = self._session.delete(
            f'{self.base_url}/datastores/{self.datastore}',
            auth=self._auth,
            timeout=self.timeout,
        )
        if response.status_code not in (200, 204, 404):
            self._raise_for_status(response, '<delete datastore>')

    def count_triples(self) -> int:
        rows = self.query('SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }')
        return int(rows[0]['n']) if rows else 0

    def close(self) -> None:
        self._session.close()

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _raise_for_status(response, sparql: str) -> None:
        if response.status_code >= 400:
            snippet = sparql if len(sparql) < 800 else sparql[:800] + ' ...'
            raise RuntimeError(
                f'RDFox request failed [status: {response.status_code}, '
                f'body: {response.text.strip()[:500]}, query: {snippet}]'
            )

    @staticmethod
    def _rows_from_bindings(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        results = payload.get('results', {})
        rows = []
        for binding in results.get('bindings', []):
            row = {}
            for var, cell in binding.items():
                row[var] = RDFoxClient._coerce(cell)
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
