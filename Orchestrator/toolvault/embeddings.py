"""
ToolVault Embeddings - Semantic vector generation and search.

Uses the same Gemini embedding-001 model as the snapshot system
(3072-dimensional vectors). The key difference: snapshots embed
the entire snapshot body, while ToolVault embeds only the DESCRIPTION
field — a focused, high-signal target for semantic retrieval.

This enables the core ToolVault promise: given a natural language
prompt like "send a text message", find the right tool (send_sms)
without the model needing to see all 41+ tool schemas.
"""

import math
import time
from typing import List, Optional, Dict, Any, Tuple

from Orchestrator.toolvault.config import (
    EMBEDDING_MODEL,
    EMBEDDING_TASK_TYPE_DOC,
    EMBEDDING_TASK_TYPE_QUERY,
    EMBEDDING_MAX_CHARS,
    EMBEDDING_MAX_RETRIES,
    KEYWORD_WEIGHT,
    SEMANTIC_WEIGHT,
    DEFAULT_SEARCH_LIMIT,
    SIMILARITY_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Embedding Generation
# ---------------------------------------------------------------------------

def generate_embedding(
    text: str,
    task_type: str = EMBEDDING_TASK_TYPE_DOC,
    max_retries: int = EMBEDDING_MAX_RETRIES,
) -> Optional[List[float]]:
    """Generate a 3072-dim embedding vector for text.

    Uses the same model and pattern as monitoring.py:generate_embedding(),
    but with configurable task_type:
      - "retrieval_document" for indexing tool descriptions
      - "retrieval_query" for search queries

    Args:
        text: Text to embed (truncated to EMBEDDING_MAX_CHARS)
        task_type: Gemini task type hint
        max_retries: Number of retry attempts on failure

    Returns:
        List of 3072 floats, or None on failure.
    """
    import google.generativeai as genai

    # Truncate if needed
    if len(text) > EMBEDDING_MAX_CHARS:
        text = text[:EMBEDDING_MAX_CHARS] + "..."

    for attempt in range(max_retries):
        try:
            result = genai.embed_content(
                model=EMBEDDING_MODEL,
                content=text,
                task_type=task_type,
            )
            return result["embedding"]
        except Exception as e:
            print(f"[TOOLVAULT-EMB] Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                print(f"[TOOLVAULT-EMB] Failed after {max_retries} attempts")
                return None


def embed_tool_description(description: str) -> Optional[List[float]]:
    """Generate an embedding specifically for a tool description.

    Uses retrieval_document task type (this text will be searched against).
    """
    return generate_embedding(description, task_type=EMBEDDING_TASK_TYPE_DOC)


def embed_query(query: str) -> Optional[List[float]]:
    """Generate an embedding for a search query.

    Uses retrieval_query task type (optimized for finding relevant docs).
    """
    return generate_embedding(query, task_type=EMBEDDING_TASK_TYPE_QUERY)


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two vectors.

    Returns 0.0-1.0 score. Same implementation as monitoring.py.
    """
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    magnitude1 = math.sqrt(sum(a * a for a in vec1))
    magnitude2 = math.sqrt(sum(b * b for b in vec2))

    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0

    return dot_product / (magnitude1 * magnitude2)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def semantic_search(
    query: str,
    tools_with_embeddings: Dict[str, Dict[str, Any]],
    limit: int = DEFAULT_SEARCH_LIMIT,
    threshold: float = SIMILARITY_THRESHOLD,
) -> List[Tuple[str, float]]:
    """Semantic search over tool embeddings.

    Args:
        query: Natural language search query
        tools_with_embeddings: {name: entry} dict (from manifest.get_tools_with_embeddings)
        limit: Maximum results to return
        threshold: Minimum cosine similarity score

    Returns:
        List of (tool_name, similarity_score) sorted by relevance.
    """
    query_vec = embed_query(query)
    if not query_vec:
        print("[TOOLVAULT-SEARCH] Query embedding failed")
        return []

    scores = []
    for name, entry in tools_with_embeddings.items():
        embedding = entry.get("embedding")
        if not embedding:
            continue

        sim = cosine_similarity(query_vec, embedding)
        if sim >= threshold:
            scores.append((name, sim))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:limit]


def keyword_search(
    query: str,
    tools: Dict[str, Dict[str, Any]],
    tool_descriptions: Dict[str, str],
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> List[Tuple[str, float]]:
    """Keyword search over tool names and descriptions.

    Simple but effective: tokenize query, score by term overlap
    with tool name and description. Complements semantic search
    for exact-match cases (e.g., searching for "gmail" finds gmail tools).

    Args:
        query: Search query
        tools: {name: entry} dict from manifest
        tool_descriptions: {name: description_text} pre-extracted
        limit: Maximum results

    Returns:
        List of (tool_name, score) sorted by relevance.
    """
    query_lower = query.lower()
    query_tokens = set(query_lower.split())

    scores = []
    for name, desc in tool_descriptions.items():
        score = 0.0
        name_lower = name.lower()
        desc_lower = desc.lower()

        # Exact name match (highest signal)
        if query_lower == name_lower:
            score += 5.0

        # Query tokens found in tool name
        name_parts = set(name_lower.replace("_", " ").split())
        name_overlap = len(query_tokens & name_parts)
        score += name_overlap * 2.0

        # Query tokens found in description
        for token in query_tokens:
            if token in desc_lower:
                score += 1.0

        # Substring match in name
        if query_lower in name_lower or name_lower in query_lower:
            score += 3.0

        # Category match (from entry)
        entry = tools.get(name, {})
        category = entry.get("category", "").lower().replace("_", " ")
        for token in query_tokens:
            if token in category:
                score += 1.5

        if score > 0:
            scores.append((name, score))

    # Normalize scores to 0-1 range
    if scores:
        max_score = max(s for _, s in scores)
        if max_score > 0:
            scores = [(name, s / max_score) for name, s in scores]

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:limit]


def hybrid_search(
    query: str,
    tools_with_embeddings: Dict[str, Dict[str, Any]],
    tool_descriptions: Dict[str, str],
    limit: int = DEFAULT_SEARCH_LIMIT,
    threshold: float = SIMILARITY_THRESHOLD,
) -> List[Tuple[str, float]]:
    """Hybrid search combining keyword (40%) and semantic (60%) scores.

    Same weighting as the snapshot hybrid search in fossils.py.
    This is the primary search interface for the ToolVault.

    Args:
        query: Natural language query
        tools_with_embeddings: {name: entry} from manifest
        tool_descriptions: {name: description} for keyword search
        limit: Max results
        threshold: Minimum combined score

    Returns:
        List of (tool_name, combined_score) sorted by relevance.
    """
    # Get candidates from both methods
    semantic_results = semantic_search(
        query, tools_with_embeddings,
        limit=limit * 3,  # Get extra candidates for merging
        threshold=0.0,    # Don't threshold individual method
    )
    keyword_results = keyword_search(
        query, tools_with_embeddings, tool_descriptions,
        limit=limit * 3,
    )

    # Build score maps
    semantic_scores = dict(semantic_results)
    keyword_scores = dict(keyword_results)

    # Combine with weights
    all_names = set(semantic_scores.keys()) | set(keyword_scores.keys())
    combined = {}

    for name in all_names:
        kw = keyword_scores.get(name, 0.0)
        sem = semantic_scores.get(name, 0.0)
        combined[name] = (KEYWORD_WEIGHT * kw) + (SEMANTIC_WEIGHT * sem)

    # Filter by threshold and sort
    results = [(name, score) for name, score in combined.items() if score >= threshold]
    results.sort(key=lambda x: x[1], reverse=True)

    Y = "\033[33m"  # Yellow
    R = "\033[0m"   # Reset
    top = results[:limit]
    print(f"{Y}[TOOLVAULT-SEARCH] Hybrid: {len(semantic_results)} semantic + "
          f"{len(keyword_results)} keyword → {len(top)} results{R}")
    for name, score in top:
        print(f"{Y}  ├─ {name:30s} score={score:.3f}{R}")

    return top
