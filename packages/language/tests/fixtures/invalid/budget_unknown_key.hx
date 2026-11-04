schema Evidence {
  passed: Bool
  detail: String
}

workflow check(input: String) -> Evidence {
  requires tool.test
  budget { steps: 5; tokens: 100 }

  let evidence = await verify.tests(input, suite: "unit")
  return evidence
}
