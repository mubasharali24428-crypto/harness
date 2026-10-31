// M5 (b): record construction. Every field is named; the order cannot matter.
module records

schema RepoFile {
  path: String
  text: String
}

schema Summary {
  text: String
  path: String
}

schema Report {
  headline: Summary
  files: List<RepoFile>
}

workflow report() -> Report {
  requires repo.read
  budget { steps: 30; tool_calls: 3; duration: 1m }

  let readme = await repo.read("README.md")
  let summary = Summary(text: readme.text, path: readme.path)
  return Report(headline: summary, files: [readme])
}
