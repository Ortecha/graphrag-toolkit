# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Classify an incoming OpenCypher statement so the store can route it.

The toolkit funnels every graph operation through ``_execute_query(cypher)``.
We split those into three buckets:

* ``noop``  – index/DDL or ``CALL`` procedures that have no SPARQL equivalent.
* ``write`` – build-path mutations (``MERGE``/``SET``/``DELETE`` with no
  ``RETURN``); handed to the write translator.
* ``read``  – ``MATCH ... RETURN`` retriever queries; handed to the read
  templates.
"""

import re

_INDEX_DDL = re.compile(r'\b(CREATE|DROP)\s+(\w+\s+)?INDEX\b', re.IGNORECASE)
_CALL = re.compile(r'\bCALL\b', re.IGNORECASE)
_RETURN = re.compile(r'\bRETURN\b', re.IGNORECASE)
_WRITE_KW = re.compile(r'\b(MERGE|DELETE|DETACH|SET|INSERT)\b', re.IGNORECASE)

NOOP = 'noop'
WRITE = 'write'
READ = 'read'


def classify(cypher: str) -> str:
    if _INDEX_DDL.search(cypher) or 'db.indexes' in cypher.lower() or _CALL.search(cypher):
        return NOOP
    if _WRITE_KW.search(cypher) and not _RETURN.search(cypher):
        return WRITE
    return READ
