// M5 (c): a loop whose termination story is budget.steps, spent once per pass.
module loops

schema RepoFile {
  path: String
  text: String
}

workflow first_path() -> String {
  requires repo.read
  budget { steps: 40; tool_calls: 3; duration: 1m }

  let files = await repo.list("docs/")
  for file in files {
    if file.path != "" {
      return file.path
    }
  }
  return ""
}
