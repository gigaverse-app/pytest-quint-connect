module lru_cache {
  type Entry = { key: str, value: int }
  type Read = Hit(int) | Miss | NoRead
  pure val CAPACITY = 2
  var entries: List[Entry]
  var lastRead: Read
  var tags: Set[str]
  var index: str -> int
  var pair: (int, str)
  pure def without(es: List[Entry], k: str): List[Entry] = es.select(e => e.key != k)
  action init = all { entries' = [], lastRead' = NoRead, tags' = Set(), index' = Map(), pair' = (0, "") }
  action store(k: str, v: int): bool = {
    val kept = without(entries, k)
    val bounded = if (kept.length() >= CAPACITY) kept.slice(1, kept.length()) else kept
    all { entries' = bounded.append({ key: k, value: v }), lastRead' = NoRead, tags' = tags.union(Set(k)), index' = index.put(k, v), pair' = (v, k) }
  }
  action fetch(k: str): bool = {
    val hits = entries.select(e => e.key == k)
    all {
      entries' = if (hits.length() == 0) entries else without(entries, k).append(hits[0]),
      lastRead' = if (hits.length() == 0) Miss else Hit(hits[0].value),
      tags' = tags, index' = index, pair' = pair
    }
  }
  action step = {
    nondet k = Set("a", "b", "c").oneOf()
    nondet v = 0.to(2).oneOf()
    any { store(k, v), fetch(k) }
  }
  val bounded = entries.length() <= CAPACITY
  run evictsTest = init.then(store("a", 1)).then(store("b", 2)).then(fetch("a")).then(store("c", 0)).then(fetch("b"))
    .expect(lastRead == Miss)
}
