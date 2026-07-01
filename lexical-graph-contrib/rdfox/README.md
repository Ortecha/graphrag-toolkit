# graphrag-toolkit-lexical-graph-rdfox

RDF/SPARQL support for the AWS GraphRAG Toolkit lexical graph, backed by
[RDFox](https://www.oxfordsemantic.tech/).

Unlike the other backends (Neo4j, Neptune, FalkorDB) which are all Labeled
Property Graph / OpenCypher engines, this backend stores the lexical graph as
**RDF triples** and answers the toolkit's queries with **SPARQL**. The toolkit's
OpenCypher is translated at the storage boundary:

* **Build-path writes** (`MERGE`/`SET` from the graph builders) → SPARQL updates.
* **Retriever reads** (`MATCH ... RETURN`) → hand-authored SPARQL templates.

## RDF model

| LPG label | RDF class | LPG edge | RDF predicate |
|---|---|---|---|
| `__Source__` | `lg:Source` | `__EXTRACTED_FROM__` | `lg:extractedFrom` |
| `__Chunk__` | `lg:Chunk` | `__PARENT__`/`__CHILD__` | `lg:parent`/`lg:child` |
| `__Topic__` | `lg:Topic` | `__PREVIOUS__`/`__NEXT__` | `lg:chunkPrevious`/`lg:statementPrevious`/`lg:next` |
| `__Statement__` | `lg:Statement` | `__BELONGS_TO__` | `lg:belongsTo` |
| `__Fact__` | `lg:Fact` | `__MENTIONED_IN__` | `lg:statementMentionedIn`/`lg:topicMentionedIn` |
| `__Entity__` | `lg:Entity` | `__SUPPORTS__` | `lg:supports` |
| `__SYS_Class__` | `lg:SysClass` | `__SUBJECT__`/`__OBJECT__` | `lg:subject`/`lg:object` |

Namespace: `lg: <https://awslabs.github.io/graphrag-toolkit/lexical#>`. Each node
is a deterministic IRI derived from its existing id; the raw id is also stored as
an `lg:id` literal.

### Edge metadata → intermediate relation nodes

RDF has no native edge metadata. The two property-bearing LPG edges become
first-class RDF resources (matching the toolkit's existing `__Fact__` /
`__SYS_Class__` n-ary style):

* `__RELATION__{value}` → an `lg:Relation` node (`lg:relSubject`, `lg:relObject`,
  `lg:value`) **plus** a direct `lg:related` triple between the entities so
  traversal stays expressible as a SPARQL property path.
* `__SYS_RELATION__{value,count}` → an `lg:SysRelation` node (`lg:sysRelSubject`,
  `lg:sysRelObject`, `lg:value`, `lg:count`).

Domain-ambiguous edges are modelled as **specialised properties** (single
domain/range) rather than union domains — e.g. `lg:statementMentionedIn` /
`lg:topicMentionedIn` (both `rdfs:subPropertyOf lg:mentionedIn`) and
`lg:chunkPrevious` / `lg:statementPrevious`.

## Usage

```python
from graphrag_toolkit.lexical_graph.storage import GraphStoreFactory
from graphrag_toolkit_contrib.lexical_graph.storage.graph.rdfox import RDFoxGraphStoreFactory

GraphStoreFactory.register(RDFoxGraphStoreFactory)

graph_store = GraphStoreFactory.for_graph_store(
    'rdfox://admin:admin@localhost:12110/graphrag-toolkit-rdf'
)
```

The vector store is unchanged — pair this with pgvector / OpenSearch / etc. as
usual.

Credentials may be supplied in the connection string, via `username`/`password`
kwargs, or the `RDFOX_USER`/`RDFOX_PASSWORD` environment variables. Use
`rdfox+s://` for HTTPS. Port defaults to `12110`.

## Status

* **Write path:** implemented (all build-path patterns) and verified against a
  live RDFox.
* **Read path:** in progress. Retriever templates are added incrementally; an
  unimplemented read raises a clear error rather than returning wrong results.
* **Not yet supported:** local-entity rewrites (run with
  `INCLUDE_LOCAL_ENTITIES=False`).

## Tests

```bash
# unit (translation; no server needed)
pytest lexical-graph-contrib/rdfox/tests/test_cypher_to_sparql_write.py -v

# live (needs a reachable RDFox; skipped otherwise)
RDFOX_URL=rdfox://admin:admin@localhost:12110/graphrag-toolkit-rdf \
  pytest lexical-graph-contrib/rdfox/tests/test_rdfox_live.py -v
```
