// pytest-quint-connect checking itself: what a pytest session leaves behind.
//
// Each `session` step is one real pytest run of the LRU example project with the
// installed plugin. `observed` is what a CI job or a person sees afterwards: the
// status and mode in .artifacts/quint-connect/results.json, and pytest's exit code.
// The promise under test: the receipt always describes the latest session, and only
// a complete, green, generated run reads as "passed" (or exits 0 under
// --quint-require-complete).
module session_receipts {
  type Event = { op: str, kind: str, strict: bool }
  type Receipt = { status: str, mode: str, exitCode: int }
  // `stage` and `ran` are for readers: reports draw the model as a state machine whose boxes
  // are what CI sees and whose arrows say which session ran (the manifest's `state_names` and
  // `step_names`). The driver is never compared on them; `observed` is what the code must show.
  type Stage = NoReceiptYet | Passed | Failed | IncompleteButGreen | IncompleteAndRejected
  type Ran =
    | NoSessionYet
    | FullRun
    | FilteredRun
    | ReplayOfOneSavedTrace
    | RunWithAPlantedBug
    | RunWithoutQuint
    | ReadTheReceiptAgain

  // full:     every case of both contract tests, correct cache
  // filtered: -k "test_lru_cache and witness"
  // faulty:   --lru-implementation stale-read (a real behavioral bug)
  // replay:   --quint-replay of a witness trace saved from an earlier run
  // noQuint:  --quint pointing at a missing executable
  pure val KINDS = Set("full", "filtered", "faulty", "replay", "noQuint")
  pure val GENERATED = "generate-and-replay"

  var event: Event
  var observed: Receipt
  // A witness trace from an earlier session in this project is available to replay.
  var traceSaved: bool
  /// What CI sees after the latest pytest session: the receipt's verdict and pytest's exit code
  var stage: Stage
  /// Which pytest session ran
  var ran: Ran

  pure def outcome(kind: str, strict: bool): Receipt =
    if (kind == "full") { status: "passed", mode: GENERATED, exitCode: 0 }
    else if (kind == "filtered") { status: "partial", mode: GENERATED, exitCode: if (strict) 1 else 0 }
    else if (kind == "replay") { status: "partial", mode: "replay-only", exitCode: if (strict) 1 else 0 }
    else { status: "failed", mode: GENERATED, exitCode: 1 }

  pure def stageOf(kind: str, strict: bool): Stage =
    if (kind == "full") Passed
    else if (kind == "filtered" or kind == "replay") { if (strict) IncompleteAndRejected else IncompleteButGreen }
    else Failed

  pure def ranOf(kind: str): Ran =
    if (kind == "full") FullRun
    else if (kind == "filtered") FilteredRun
    else if (kind == "replay") ReplayOfOneSavedTrace
    else if (kind == "faulty") RunWithAPlantedBug
    else RunWithoutQuint

  action init = all {
    event' = { op: "init", kind: "", strict: false },
    observed' = { status: "none", mode: "none", exitCode: -1 },
    traceSaved' = false,
    stage' = NoReceiptYet,
    ran' = NoSessionYet,
  }

  action session(kind: str, strict: bool): bool = all {
    or { kind != "replay", traceSaved },
    event' = { op: "session", kind: kind, strict: strict },
    observed' = outcome(kind, strict),
    traceSaved' = or { traceSaved, kind == "full", kind == "filtered", kind == "faulty" },
    stage' = stageOf(kind, strict),
    ran' = ranOf(kind),
  }

  action inspect = all {
    event' = { op: "inspect", kind: "", strict: false },
    observed' = observed,
    traceSaved' = traceSaved,
    stage' = stage,
    ran' = ReadTheReceiptAgain,
  }

  action step = {
    nondet kind = KINDS.oneOf()
    nondet strict = Set(true, false).oneOf()
    session(kind, strict)
  }

  // Never "passed" unless the latest session generated fresh histories and exited 0;
  // under --quint-require-complete, a nonzero exit for anything less.
  val honest = and {
    observed.status != "passed" or (observed.mode == GENERATED and observed.exitCode == 0),
    observed.status == "passed" or observed.status == "none" or observed.exitCode != 0
      or event.op != "session" or not(event.strict),
  }

  run staleSuccessIsReplacedTest = init.then(session("full", true)).then(session("noQuint", false))
    .then(all { assert(observed.status == "failed" and observed.exitCode == 1), inspect })

  run filteredRunIsNeverPassedTest = init.then(session("filtered", false)).then(session("filtered", true))
    .then(all { assert(observed.status == "partial" and observed.exitCode == 1), inspect })

  run replayIsNeverCompleteTest = init.then(session("full", false)).then(session("replay", true))
    .then(all { assert(observed.mode == "replay-only" and observed.exitCode == 1), inspect })

  run faultAfterSuccessFailsTest = init.then(session("full", false)).then(session("faulty", true))
    .then(all { assert(observed.status == "failed"), inspect })
}
