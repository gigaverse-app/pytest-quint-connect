"""Three caches with one interface: the original, a refactor, and a cache with a bug."""

from collections import OrderedDict


class LRUCache:
    """A least-recently-used cache backed by a list, oldest entry first."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._entries: list[tuple[str, int]] = []

    def _index(self, key: str) -> int | None:
        return next((i for i, (k, _) in enumerate(self._entries) if k == key), None)

    def put(self, key: str, value: int) -> None:
        index = self._index(key)
        if index is not None:
            del self._entries[index]
        elif len(self._entries) >= self.capacity:
            del self._entries[0]
        self._entries.append((key, value))

    def get(self, key: str) -> int | None:
        index = self._index(key)
        if index is None:
            return None
        entry = self._entries.pop(index)
        self._entries.append(entry)
        return entry[1]

    def items(self) -> list[tuple[str, int]]:
        return list(self._entries)


class OrderedLRUCache:
    """The same cache rewritten on OrderedDict. Behavior is meant to be identical."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._entries: OrderedDict[str, int] = OrderedDict()

    def put(self, key: str, value: int) -> None:
        if key in self._entries:
            self._entries.move_to_end(key)
        elif len(self._entries) >= self.capacity:
            self._entries.popitem(last=False)
        self._entries[key] = value

    def get(self, key: str) -> int | None:
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return self._entries[key]

    def items(self) -> list[tuple[str, int]]:
        return list(self._entries.items())


class StaleReadLRUCache(LRUCache):
    """A plausible bug: reads do not refresh recency, so a just-read entry can be evicted next."""

    def get(self, key: str) -> int | None:
        index = self._index(key)
        return None if index is None else self._entries[index][1]


IMPLEMENTATIONS = {"list": LRUCache, "ordered": OrderedLRUCache, "stale-read": StaleReadLRUCache}
