// A two-entry least-recently-used cache. `event` names each step for the driver;
// `observed` is what the driver must read back from the real cache after it.
// `fill` and `change` are for readers: reports draw the model as a state machine whose
// boxes are fill levels and whose arrows say what each step did (the manifest's
// `state_names` and `step_names`). The driver is never compared on them.
module lru_cache {
  type Entry = { key: str, value: int }
  type Read = Hit(int) | Miss | NoRead
  type Event = { op: str, key: str, value: int }
  type View = { entries: List[Entry], lastRead: Read }
  type Fill = Empty | PartlyFilled | Full
  type Change =
    | Started
    | AddedEntry
    | ReplacedValue
    | EvictedLeastRecentlyUsed
    | ReadHitMovedToMostRecent
    | ReadMissed
    | NothingChanged

  pure val CAPACITY = 2

  var event: Event
  // entries are ordered least recently used first
  var observed: View
  /// How full the cache is after each step
  var fill: Fill
  /// What each step did to the cache
  var change: Change

  pure def without(entries: List[Entry], key: str): List[Entry] = entries.select(e => e.key != key)

  pure def fillOf(entries: List[Entry]): Fill =
    if (entries.length() == 0) Empty else if (entries.length() < CAPACITY) PartlyFilled else Full

  action init = all {
    event' = { op: "init", key: "", value: 0 },
    observed' = { entries: [], lastRead: NoRead },
    fill' = Empty,
    change' = Started,
  }

  action store(key: str, value: int): bool = {
    val kept = without(observed.entries, key)
    val evicts = kept.length() >= CAPACITY
    val room = if (evicts) kept.slice(1, kept.length()) else kept
    val stored = room.append({ key: key, value: value })
    all {
      event' = { op: "store", key: key, value: value },
      observed' = { entries: stored, lastRead: NoRead },
      fill' = fillOf(stored),
      change' = if (kept.length() < observed.entries.length()) ReplacedValue
        else if (evicts) EvictedLeastRecentlyUsed
        else AddedEntry,
    }
  }

  action fetch(key: str): bool = {
    val hits = observed.entries.select(e => e.key == key)
    all {
      event' = { op: "fetch", key: key, value: 0 },
      observed' = if (hits.length() == 0) { ...observed, lastRead: Miss }
        else { entries: without(observed.entries, key).append(hits[0]), lastRead: Hit(hits[0].value) },
      fill' = fill,
      change' = if (hits.length() == 0) ReadMissed else ReadHitMovedToMostRecent,
    }
  }

  // A read-only step, so that a witness can end in an assertion without repeating an action.
  action inspect = all {
    event' = { op: "inspect", key: "", value: 0 },
    observed' = observed,
    fill' = fill,
    change' = NothingChanged,
  }

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
