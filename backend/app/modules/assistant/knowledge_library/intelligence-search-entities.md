# Entities and AI Search in Nova

Keywords: entity, entities, entitas, AI Search, pencarian AI, vector search, hybrid search, lexical search, semantic search, embedding, search index.

An Entity gives a business identity to a source table or view. It records a source relation and stable key columns, so Intelligence features can refer to the same customer, order, or other object. Create it from the Entities tab on an Intelligence page; choose an authorized source relation and its key columns. Entity metadata appears alongside tables and views in Database Explorer. Entity keys must exist in the source; creating an Entity does not copy or grant access to its data.

AI Search finds source records using text. A lexical index matches words; a semantic index compares text embeddings; hybrid mode combines both rankings. Use the AI Search page at `/ai-search` to create an index from authorized source columns, watch a version build, query it, evaluate ranking, rebuild, retry, and activate a reviewed version. Lexical mode does not require an embedding model. Semantic and hybrid modes need an active embedding model alias from AI Providers. Results are checked against the source under the caller's StarRocks identity before content is returned.

The search projection is versioned. A new build does not replace the active version until activation; unchanged content can reuse existing vectors during reconciliation. Search evaluation records ranking measures such as Precision@K and MRR. Sources are rechecked on a schedule, so very large, fast-changing sources can lag behind.

For Neva: explain these concepts with `search_knowledge`. To actually search an index, use the consent-gated `ai_search` tool with an index name and query; do not turn a question such as “AI Search itu untuk apa?” into a search operation. The `semantic_search` tool used by Agent Studio for semantic models is a different capability from searching an AI Search Index.

