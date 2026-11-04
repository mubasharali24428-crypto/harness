// M5 (a): enum values in source. A member is a value of the enum's own type.
module enum_values

enum Severity {
  low
  high
}

schema RepoFile {
  path: String
  text: String
}

schema Finding {
  severity: Severity
  note: String
}

workflow triage(input: Finding) -> Finding {
  requires repo.read
  budget { steps: 20; tool_calls: 2; duration: 1m }

  let readme = await repo.read("README.md")
  let urgent = input.severity == Severity.high
  if urgent {
    ensure readme.text != ""
  }
  return input
}
