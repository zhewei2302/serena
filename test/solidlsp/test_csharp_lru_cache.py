"""Tests for LRUCache class used in CSharpLanguageServer."""

from solidlsp.language_servers.csharp_language_server import LRUCache


class TestLRUCache:
    """Test LRU cache implementation."""

    def test_basic_set_and_get(self) -> None:
        """Test basic set and get operations."""
        cache: LRUCache = LRUCache(maxsize=3)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3

        assert cache["a"] == 1
        assert cache["b"] == 2
        assert cache["c"] == 3

    def test_eviction_on_maxsize(self) -> None:
        """Test that oldest items are evicted when maxsize is exceeded."""
        cache: LRUCache = LRUCache(maxsize=3)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3
        cache["d"] = 4  # This should evict "a"

        assert "a" not in cache
        assert cache["b"] == 2
        assert cache["c"] == 3
        assert cache["d"] == 4

    def test_access_updates_order(self) -> None:
        """Test that accessing an item moves it to the end (most recent)."""
        cache: LRUCache = LRUCache(maxsize=3)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3

        # Access "a" to make it most recently used
        _ = cache["a"]

        # Add new item - should evict "b" (oldest), not "a"
        cache["d"] = 4

        assert "a" in cache
        assert "b" not in cache
        assert cache["c"] == 3
        assert cache["d"] == 4

    def test_set_existing_updates_order(self) -> None:
        """Test that setting an existing key moves it to the end."""
        cache: LRUCache = LRUCache(maxsize=3)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3

        # Update "a" to make it most recently used
        cache["a"] = 10

        # Add new item - should evict "b" (oldest), not "a"
        cache["d"] = 4

        assert cache["a"] == 10
        assert "b" not in cache
        assert cache["c"] == 3
        assert cache["d"] == 4

    def test_get_method_does_not_update_order(self) -> None:
        """Test that the get() method doesn't change order (unlike __getitem__)."""
        cache: LRUCache = LRUCache(maxsize=3)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3

        # Use get() which should NOT update order
        result = cache.get("a")
        assert result == 1

        # Add new item - should evict "a" (oldest) because get() didn't update order
        cache["d"] = 4

        assert "a" not in cache
        assert cache["b"] == 2

    def test_get_returns_default_for_missing_key(self) -> None:
        """Test that get() returns default value for missing keys."""
        cache: LRUCache = LRUCache(maxsize=3)
        cache["a"] = 1

        assert cache.get("missing") is None
        assert cache.get("missing", "default") == "default"

    def test_len(self) -> None:
        """Test that len() returns correct size."""
        cache: LRUCache = LRUCache(maxsize=5)
        assert len(cache) == 0

        cache["a"] = 1
        assert len(cache) == 1

        cache["b"] = 2
        cache["c"] = 3
        assert len(cache) == 3

    def test_contains(self) -> None:
        """Test the 'in' operator."""
        cache: LRUCache = LRUCache(maxsize=3)
        cache["a"] = 1

        assert "a" in cache
        assert "b" not in cache

    def test_tuple_values(self) -> None:
        """Test storing tuple values (like Razor virtual document cache)."""
        cache: LRUCache = LRUCache(maxsize=3)
        cache["file:///test.razor"] = (1, "generated content", "file:///test.razor.g.cs")

        version, content, uri = cache["file:///test.razor"]
        assert version == 1
        assert content == "generated content"
        assert uri == "file:///test.razor.g.cs"
