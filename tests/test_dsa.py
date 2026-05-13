import pytest
from dsa import LRUCache, BookTrie, BookGraph

def test_lru_cache():
    cache = LRUCache(capacity=2)
    cache.put(1, "Book 1")
    cache.put(2, "Book 2")
    assert cache.get(1) == "Book 1"
    
    # Cache is full, putting 3 should evict 2 since 1 was just accessed
    cache.put(3, "Book 3")
    assert cache.get(2) is None
    assert cache.get(3) == "Book 3"
    assert cache.get(1) == "Book 1"

def test_trie():
    trie = BookTrie()
    trie.insert("Data Structures in Python", 1, "Guido")
    trie.insert("Database Management", 2, "Codd")
    
    res = trie.search_prefix("Data")
    assert len(res) == 2
    titles = [r['title'] for r in res]
    assert "Data Structures in Python" in titles
    assert "Database Management" in titles
    
    res2 = trie.search_prefix("Structures")
    assert len(res2) == 1
    assert res2[0]['id'] == 1

def test_graph_bfs():
    graph = BookGraph()
    # Book 1 connected to Book 2 (weight 1)
    graph.add_edge(1, 2, 1)
    # Book 2 connected to Book 3 (weight 2)
    graph.add_edge(2, 3, 2)
    
    recs = graph.get_recommendations(1)
    # Should recommend 2 (direct) and 3 (via 2)
    assert 2 in recs
    assert 3 in recs
    assert len(recs) == 2
