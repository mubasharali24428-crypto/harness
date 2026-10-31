// Two host reads against a budget that allows one: the second must stop the
// run and name the limit. docs/hx-execution-architecture.md §3, step 6.
module stops_at_budget

schema RepoFile {
  path: String
  text: String
}

workflow twice() -> RepoFile {
  requires repo.read
  budget { steps: 20; tool_calls: 1; duration: 1m }
  let first = await repo.read("README.md")
  let second = await repo.read("README.md")
  return second
}
