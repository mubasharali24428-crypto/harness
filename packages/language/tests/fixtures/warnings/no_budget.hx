// Well formed, but noisy: no budget, a spawn with no tasks limit, an unused grant.
schema RepoFile {
  path: String
  text: String
}

workflow read() -> RepoFile {
  requires repo.read, tool.test

  let a = spawn repo.read("README.md")
  let file = await a
  return file
}
