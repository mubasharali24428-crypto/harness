schema Evidence {
  passed: Bool
  detail: String
}

workflow check(input: String) -> Evidence {
  requires tool.test
  budget { steps: 5 }

  let evidence = await verify.tests(input, "unit", "extra")
  return evidence
}
