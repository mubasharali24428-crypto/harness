schema Evidence {
  passed: Bool
  detail: String
}

workflow check(input: String) -> Evidence {
  requires repo.write
  budget { steps: 10 }

  let evidence = await verify.tests(input, suite: "unit")
  return evidence
}
