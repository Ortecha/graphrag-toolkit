# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""RDFox-backed :class:`GraphStore` for the lexical graph.

Stores the lexical graph as RDF triples in an RDFox data store and answers the
toolkit's OpenCypher operations by translating them to SPARQL:

* build-path writes  -> SPARQL updates (:mod:`cypher_to_sparql_write`)
* retriever reads    -> SPARQL templates (:mod:`sparql_templates`)

The single abstract method a backend must implement is ``_execute_query``; the
framework's ``execute_query`` / ``execute_query_with_retry`` wrap it (including
the ``QueryTree`` decomposition, which arrives here as plain string queries).
"""

import logging
import time
import uuid
from typing import Any, List, Optional

from llama_index.core.bridge.pydantic import PrivateAttr

from graphrag_toolkit.lexical_graph.storage.graph import GraphStore, NodeId, format_id

from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox.rdfox_client import RDFoxClient
from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox.query_router import classify, NOOP, WRITE
from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox.cypher_to_sparql_write import translate_write
from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox.sparql_templates import execute_read

logger = logging.getLogger(__name__)

DEFAULT_PORT = 12110


class RDFoxGraphStore(GraphStore):
    """Graph store that persists the lexical graph as RDF in RDFox."""

    base_url: str
    datastore: str
    username: Optional[str] = None
    password: Optional[str] = None

    _client: Optional[Any] = PrivateAttr(default=None)

    def __init__(self,
                 base_url: str,
                 datastore: str,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 **kwargs) -> None:
        super().__init__(
            base_url=base_url,
            datastore=datastore,
            username=username,
            password=password,
            **kwargs,
        )

    def __getstate__(self):
        self._client = None
        return super().__getstate__()

    @property
    def client(self) -> RDFoxClient:
        if self._client is None:
            self._client = RDFoxClient(
                base_url=self.base_url,
                datastore=self.datastore,
                username=self.username,
                password=self.password,
            )
        return self._client

    def node_id(self, id_name: str) -> NodeId:
        # Property-based ids (like Neo4j); the translator/templates interpret
        # the rendered id form (``sourceId`` / ``l.statementId``).
        return format_id(id_name)

    def init(self, graph_store=None):
        # RDFox auto-indexes triples, so there are no indexes to create; just
        # make sure the target data store exists.
        target = graph_store or self
        try:
            if not target.client.datastore_exists():
                target.client.create_datastore()
        except Exception as e:  # pragma: no cover - best-effort bootstrap
            logger.warning(f'RDFox datastore bootstrap check failed: {e}')

    def _execute_query(self,
                       cypher: str,
                       parameters: Optional[dict] = None,
                       correlation_id: Any = None) -> List[Any]:
        parameters = parameters or {}

        query_id = uuid.uuid4().hex[:5]
        log_entry = self.log_formatting.format_log_entry(
            self._logging_prefix(query_id, correlation_id), cypher, parameters,
        )
        logger.debug(f'[{log_entry.query_ref}] Query: [query: {log_entry.query}, '
                     f'parameters: {log_entry.parameters}]')

        start = time.time()
        kind = classify(cypher)

        if kind == NOOP:
            results: List[Any] = []
        elif kind == WRITE:
            update = translate_write(cypher, parameters)
            if update:
                self.client.update(update)
            results = []
        else:
            results = execute_read(self.client, cypher, parameters)

        if logger.isEnabledFor(logging.DEBUG):
            elapsed = int((time.time() - start) * 1000)
            logger.debug(f'[{log_entry.query_ref}] {elapsed}ms ({kind}) '
                         f'-> {len(results)} row(s)')

        return results

    def __exit__(self, exception_type, exception_value, traceback):
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None
        return False
