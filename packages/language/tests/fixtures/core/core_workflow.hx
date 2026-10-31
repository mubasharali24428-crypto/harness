// A well-formed agent workflow that the core profile must refuse.
module escalation

schema Issue {
  id: String
}

schema Evidence {
  passed: Bool
  detail: String
}

workflow check(input: Issue) -> Evidence {
  requires tool.test
  budget { steps: 20; tool_calls: 2; duration: 2m }

  let evidence = await verify.tests(input, suite: "unit")
  ensure evidence.passed
  return evidence
}
