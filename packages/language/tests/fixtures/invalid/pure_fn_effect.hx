schema RepoFile {
  path: String
  text: String
}

fn read_it() -> RepoFile {
  let file = await repo.read("README.md")
  return file
}
