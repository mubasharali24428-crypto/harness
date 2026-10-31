schema RepoFile {
  path: String
  text: String
}

workflow scan() -> Int {
  requires repo.read
  budget { tool_calls: 3; duration: 1m }
  let files = await repo.list("docs/")
  for file in files {
    ensure file.path != ""
  }
  return 0
}
