# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Factory that creates an :class:`RDFoxGraphStore` from a connection string.

Connection string:  ``rdfox://[user:pass@]host[:port]/<datastore>``
(use ``rdfox+s://`` for HTTPS). Credentials may also come from the
``username``/``password`` kwargs or the ``RDFOX_USER``/``RDFOX_PASSWORD``
environment variables. Port defaults to 12110.
"""

import logging
import os
from urllib.parse import urlparse

from graphrag_toolkit.lexical_graph.storage.graph import (
    GraphStoreFactoryMethod,
    GraphStore,
    get_log_formatting,
)

from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox.rdfox_graph_store import (
    RDFoxGraphStore,
    DEFAULT_PORT,
)

logger = logging.getLogger(__name__)

RDFOX_SCHEMES = ('rdfox', 'rdfox+s')
DEFAULT_DATASTORE = 'graphrag'


class RDFoxGraphStoreFactory(GraphStoreFactoryMethod):

    def try_create(self, graph_info: str, **kwargs) -> GraphStore:
        if not isinstance(graph_info, str):
            return None

        parsed = urlparse(graph_info)
        if parsed.scheme not in RDFOX_SCHEMES:
            return None

        secure = parsed.scheme == 'rdfox+s'
        host = parsed.hostname or 'localhost'
        port = parsed.port or DEFAULT_PORT
        datastore = (parsed.path or '').lstrip('/') or kwargs.pop('datastore', None) or DEFAULT_DATASTORE

        username = parsed.username or kwargs.pop('username', None) or os.environ.get('RDFOX_USER')
        password = parsed.password or kwargs.pop('password', None) or os.environ.get('RDFOX_PASSWORD')

        scheme = 'https' if secure else 'http'
        base_url = f'{scheme}://{host}:{port}'

        kwargs.pop('config', None)  # accepted by other factories; not used here

        logger.debug(f'Opening RDFox graph store [base_url: {base_url}, datastore: {datastore}]')

        return RDFoxGraphStore(
            base_url=base_url,
            datastore=datastore,
            username=username,
            password=password,
            log_formatting=get_log_formatting(kwargs),
        )
