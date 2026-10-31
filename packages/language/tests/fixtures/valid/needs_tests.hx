// Needs a Section 5 sandbox. The local read-only bridge must refuse it with a
// code rather than pretend it ran: docs/hx-execution-architecture.md §3, step 5.
module needs_tests

schema Evidence {
  passed: Bool
  detail: String
}

workflow tests() -> Evidence {
  requires tool.test
  budget { steps: 20; tool_calls: 2; duration: 1m }
  let evidence = await verify.tests("origin/main", suite: "unit")
  return evidence
}
