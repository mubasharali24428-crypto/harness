schema RepoFile {
  path: String
  text: String
}

workflow read_one() -> RepoFile {
  requires repo.read
  budget { steps: 10; tasks: 1 }

  let a = spawn repo.read("README.md")
  let file = await repo.read("docs/architecture-map.md")
  return file
}
