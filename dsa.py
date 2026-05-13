import collections
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple, Set

# ==========================================
# 1. LRU Cache (Hash Map + Doubly Linked List)
# ==========================================
class Node:
    def __init__(self, key: Any, value: Any) -> None:
        self.key = key
        self.value = value
        self.prev: Optional['Node'] = None
        self.next: Optional['Node'] = None

class LRUCache:
    """O(1) Custom LRU Cache for expensive DB queries (like book details)"""
    def __init__(self, capacity: int = 100) -> None:
        self.capacity = capacity
        self.cache: Dict[Any, Node] = {}  # Hash map: key -> Node
        self.head = Node(0, 0)
        self.tail = Node(0, 0)
        self.head.next = self.tail
        self.tail.prev = self.head
        self.lock = Lock()
        
    def _add_node(self, node: Node) -> None:
        node.prev = self.head
        node.next = self.head.next
        if self.head.next:
            self.head.next.prev = node
        self.head.next = node
        
    def _remove_node(self, node: Node) -> None:
        prev = node.prev
        nxt = node.next
        if prev and nxt:
            prev.next = nxt
            nxt.prev = prev
        
    def _move_to_head(self, node: Node) -> None:
        self._remove_node(node)
        self._add_node(node)
        
    def _pop_tail(self) -> Node:
        res = self.tail.prev
        if res and res != self.head:
            self._remove_node(res)
            return res
        return self.tail # fallback, shouldn't be reached in valid usage
        
    def get(self, key: Any) -> Optional[Any]:
        with self.lock:
            if key in self.cache:
                node = self.cache[key]
                self._move_to_head(node)
                return node.value
            return None
            
    def put(self, key: Any, value: Any) -> None:
        with self.lock:
            if key in self.cache:
                node = self.cache[key]
                node.value = value
                self._move_to_head(node)
            else:
                new_node = Node(key, value)
                self.cache[key] = new_node
                self._add_node(new_node)
                if len(self.cache) > self.capacity:
                    tail = self._pop_tail()
                    if tail.key in self.cache:
                        del self.cache[tail.key]

    def invalidate(self, key: Any) -> None:
        with self.lock:
            if key in self.cache:
                node = self.cache[key]
                self._remove_node(node)
                del self.cache[key]

# ==========================================
# 2. Trie (Prefix Tree) for Autocomplete
# ==========================================
class TrieNode:
    def __init__(self) -> None:
        self.children: Dict[str, 'TrieNode'] = {}
        self.is_end_of_word: bool = False
        self.book_data: List[Dict[str, Any]] = []

class BookTrie:
    """O(L) Prefix Search for instant autocomplete"""
    def __init__(self) -> None:
        self.root = TrieNode()
        self.lock = Lock()
        
    def insert(self, title: str, book_id: int, author: str) -> None:
        with self.lock:
            words = title.lower().split()
            for start_idx in range(len(words)):
                curr_node = self.root
                suffix = " ".join(words[start_idx:])
                for char in suffix:
                    if char not in curr_node.children:
                        curr_node.children[char] = TrieNode()
                    curr_node = curr_node.children[char]
                    book_entry = {"id": book_id, "title": title, "author": author}
                    
                    exists = any(b["id"] == book_id for b in curr_node.book_data)
                    if not exists:
                        curr_node.book_data.append(book_entry)
                        if len(curr_node.book_data) > 6:
                            curr_node.book_data.pop(0)

    def search_prefix(self, prefix: str) -> List[Dict[str, Any]]:
        if not prefix: return []
        with self.lock:
            node = self.root
            for char in prefix.lower():
                if char not in node.children:
                    return []
                node = node.children[char]
            return node.book_data[::-1]

# ==========================================
# 3. Graph & BFS for Book Recommendations
# ==========================================
class BookGraph:
    """Adjacency List Graph for finding related books via BFS"""
    def __init__(self) -> None:
        self.graph: Dict[int, List[Tuple[int, int]]] = collections.defaultdict(list)
        self.lock = Lock()
        
    def add_edge(self, book1_id: int, book2_id: int, weight: int = 1) -> None:
        with self.lock:
            def _update_edge(b1: int, b2: int, w: int) -> None:
                for i, (n, current_w) in enumerate(self.graph[b1]):
                    if n == b2:
                        self.graph[b1][i] = (n, current_w + w)
                        return
                self.graph[b1].append((b2, w))

            _update_edge(book1_id, book2_id, weight)
            _update_edge(book2_id, book1_id, weight)
            
    def get_recommendations(self, start_book_id: int, limit: int = 4) -> List[int]:
        """BFS to find related books up to depth 2"""
        with self.lock:
            if start_book_id not in self.graph:
                return []
                
            visited: Set[int] = set([start_book_id])
            queue: collections.deque = collections.deque([(start_book_id, 0, 0)])
            candidates: List[Tuple[int, int]] = []
            
            while queue:
                curr_node, depth, acc_weight = queue.popleft()
                
                if depth > 0:
                    candidates.append((curr_node, acc_weight))
                
                if depth >= 2:
                    continue
                    
                for neighbor, weight in sorted(self.graph[curr_node], key=lambda x: x[1], reverse=True):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, depth + 1, acc_weight + weight))
            
            candidates.sort(key=lambda x: x[1], reverse=True)
            return [c[0] for c in candidates[:limit]]

# Global instances
catalogue_cache = LRUCache(capacity=200)
search_trie = BookTrie()
recommendation_graph = BookGraph()
