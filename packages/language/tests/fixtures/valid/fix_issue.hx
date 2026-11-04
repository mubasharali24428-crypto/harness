// The target workflow from the language plan, complete and checkable.
module maintenance

schema Issue {
  id: String
  title: String
  body: String
}

schema ChangeProposal {
  summary: String
  patch: String
}

schema RepoFile {
  path: String
  text: String
}

schema Patch {
  id: String
  diff: String
}

schema Evidence {
  passed: Bool
  detail: String
}

schema ReviewBundle {
  proposal: ChangeProposal
  evidence: Evidence
}

workflow fix_issue(input: Issue) -> ReviewBundle {
  requires repo.read, repo.propose_patch, tool.test, model.invoke
  budget { steps: 200; tool_calls: 12; tasks: 4; duration: 10m; cost: 2.00 USD }
  agent developer: Agent<ChangeProposal> { role: "developer" }

  let context = await repo.read("README.md")
  let plan = await developer.plan(input)
  let proposal = await developer.propose(plan)
  let patch = await repo.propose_patch(proposal)
  let evidence = await verify.tests(proposal, suite: "unit")
  ensure evidence.passed
  return review.bundle(proposal, evidence)
}
