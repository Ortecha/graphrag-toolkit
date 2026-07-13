# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the ontology helpers and NamespaceConfig validation."""

import pytest

from graphrag_toolkit_contrib.lexical_graph.storage.graph.sparql.ontology import (
    NamespaceConfig,
    sparql_literal,
)


def test_invalid_main_prefix_rejected():
    with pytest.raises(ValueError):
        NamespaceConfig(prefix='1bad')


def test_invalid_extra_prefix_rejected():
    with pytest.raises(ValueError):
        NamespaceConfig(extra_prefixes={'1bad': 'https://ex.test/schema#'})


def test_extra_prefix_conflicting_with_main_rejected():
    with pytest.raises(ValueError):
        NamespaceConfig(prefix='lg', extra_prefixes={'lg': 'https://different.test/schema#'})


def test_namespace_without_separator_gets_one_appended():
    ns = NamespaceConfig(schema_namespace='https://ex.test/schema',
                         instance_namespace='https://ex.test/data')
    assert ns.schema_namespace == 'https://ex.test/schema#'
    assert ns.instance_namespace == 'https://ex.test/data/'


@pytest.mark.parametrize('value,expected', [
    (None, None),
    (True, 'true'),
    (False, 'false'),
    (7, '7'),
    (1.5, repr(1.5)),
    ('x', '"x"'),
])
def test_sparql_literal_renders_each_type(value, expected):
    assert sparql_literal(value) == expected
