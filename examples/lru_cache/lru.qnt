// A two-entry least-recently-used cache. `event` names each step for the driver;
// `observed` is what the driver must read back from the real cache after it.
module lru_cache {
  type Entry = { key: str, value: int }
  type Read = Hit(int) | Miss | NoRead
  type Event = { op: str, key: str, value: int }
  type View = { entries: List[Entry], lastRead: Read }

  pure val CAPACITY = 2

  var event: Event
  // entries are ordered least recently used first
  var observed: View

  pure def without(entries: List[Entry], key: str): List[Entry] = entries.select(e => e.key != key)

  action init = all {
    event' = { op: "init", key: "", value: 0 },
    observed' = { entries: [], lastRead: NoRead },
  }

  action store(key: str, value: int): bool = {
    val kept = without(observed.entries, key)
    val room = if (kept.length() >= CAPACITY) kept.slice(1, kept.length()) else kept
    all {
      event' = { op: "store", key: key, value: value },
      observed' = { entries: room.append({ key: key, value: value }), lastRead: NoRead },
    }
  }

  action fetch(key: str): bool = {
    val hits = observed.entries.select(e => e.key == key)
    all {
      event' = { op: "fetch", key: key, value: 0 },
      observed' = if (hits.length() == 0) { ...observed, lastRead: Miss }
        else { entries: without(observed.entries, key).append(hits[0]), lastRead: Hit(hits[0].value) },
    }
  }

  // A read-only step, so that a witness can end in an assertion without repeating an action.
  action inspect = all { event' = { op: "inspect", key: "", value: 0 }, observed' = observed }

  action step = {
    nondet key = Set("a", "b", "c").oneOf()
    nondet value = 0.to(2).oneOf()
    any { store(key, value), fetch(key) }
  }

  val withinCapacity = observed.entries.length() <= CAPACITY

  run evictsLeastRecentlyUsedTest = init.then(store("a", 1)).then(store("b", 2)).then(fetch("a"))
    .then(store("c", 0))
    .then(all { assert(observed.entries == [{ key: "a", value: 1 }, { key: "c", value: 0 }]), inspect })

  run overwriteRefreshesTest = init.then(store("a", 1)).then(store("b", 2)).then(store("a", 3))
    .then(store("c", 0))
    .then(all { assert(observed.entries == [{ key: "a", value: 3 }, { key: "c", value: 0 }]), inspect })

  run missKeepsContentsTest = init.then(store("a", 1)).then(fetch("b"))
    .then(all { assert(observed.lastRead == Miss and observed.entries.length() == 1), inspect })
}
