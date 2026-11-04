// Two reads in flight at once, bounded by budget.tasks.
module concurrent

schema RepoFile {
  path: String
  text: String
}

schema Summary {
  text: String
}

workflow read_two() -> Summary {
  requires model.invoke, repo.read
  budget { steps: 50; tool_calls: 3; tasks: 2; duration: 1m }
  agent summarizer: Agent<Summary> { role: "summarizer" }

  let a = spawn repo.read("README.md")
  let b = spawn repo.read("docs/architecture-map.md")
  let first = await a
  let second = await b
  let summary = await summarizer.plan(first)
  ensure first == second or second.path == "docs/architecture-map.md"
  return summary
}
