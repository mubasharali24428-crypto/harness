// The smallest useful run: one allow-listed host read, checked and evidenced.
// Used by the demonstration in docs/hx-execution-architecture.md §3.
module read_one

schema RepoFile {
  path: String
  text: String
}

workflow read_one() -> RepoFile {
  requires repo.read
  budget { steps: 20; tool_calls: 2; duration: 1m }
  let readme = await repo.read("README.md")
  ensure readme.text != ""
  return readme
}
