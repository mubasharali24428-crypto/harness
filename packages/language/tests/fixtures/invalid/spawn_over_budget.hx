schema RepoFile {
  path: String
  text: String
}

workflow read_two() -> RepoFile {
  requires repo.read
  budget { steps: 10; tasks: 1 }

  let a = spawn repo.read("README.md")
  let b = spawn repo.read("docs/architecture-map.md")
  let first = await a
  let second = await b
  return first
}
