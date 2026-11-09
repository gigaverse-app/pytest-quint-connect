// A two-entry least-recently-used cache. `event` names each step for the driver;
// `observed` is what the driver must read back from the real cache after it;
// `fill` names the cache's state for a reader: empty, partly filled, or full.
module lru_cache {
  type Entry = { key: str, value: int }
  type Read = Hit(int) | Miss | NoRead
  type Event = { op: str, key: str, value: int }
  type View = { entries: List[Entry], lastRead: Read }
  // The states a reader would name. Reports draw the model's state machine in these names
  // (the manifest's `state_names`); the driver is never compared on them.
  type Fill = Empty | PartlyFilled | Full

  pure val CAPACITY = 2

  var event: Event
  // entries are ordered least recently used first
  var observed: View
  var fill: Fill

  pure def without(entries: List[Entry], key: str): List[Entry] = entries.select(e => e.key != key)

  pure def fillOf(entries: List[Entry]): Fill =
    if (entries.length() == 0) Empty else if (entries.length() < CAPACITY) PartlyFilled else Full

  action init = all {
    event' = { op: "init", key: "", value: 0 },
    observed' = { entries: [], lastRead: NoRead },
    fill' = Empty,
  }

  action store(key: str, value: int): bool = {
    val kept = without(observed.entries, key)
    val room = if (kept.length() >= CAPACITY) kept.slice(1, kept.length()) else kept
    val stored = room.append({ key: key, value: value })
    all {
      event' = { op: "store", key: key, value: value },
      observed' = { entries: stored, lastRead: NoRead },
      fill' = fillOf(stored),
    }
  }

  action fetch(key: str): bool = {
    val hits = observed.entries.select(e => e.key == key)
    all {
      event' = { op: "fetch", key: key, value: 0 },
      observed' = if (hits.length() == 0) { ...observed, lastRead: Miss }
        else { entries: without(observed.entries, key).append(hits[0]), lastRead: Hit(hits[0].value) },
      fill' = fill,
    }
  }

  // A read-only step, so that a witness can end in an assertion without repeating an action.
  action inspect = all { event' = { op: "inspect", key: "", value: 0 }, observed' = observed, fill' = fill }

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
