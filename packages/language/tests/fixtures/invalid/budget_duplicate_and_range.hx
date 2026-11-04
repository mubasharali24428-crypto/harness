schema Evidence {
  passed: Bool
  detail: String
}

workflow check(input: String) -> Evidence {
  requires tool.test
  budget { steps: 0; steps: 10; duration: 48h; cost: 2.00 EUR }

  let evidence = await verify.tests(input, suite: "unit")
  return evidence
}
