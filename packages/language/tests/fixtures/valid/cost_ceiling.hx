// The same run as `stops_at_budget`, bounded by money instead of calls: two reads
// against a ceiling that pays for one. The host supplies the price table, because
// a program that could name its own prices could raise its own ceiling.
// docs/hx-language-spec.md §8, milestone M6.
module cost_ceiling

schema RepoFile {
  path: String
  text: String
}

workflow twice() -> RepoFile {
  requires repo.read
  budget { steps: 20; tool_calls: 2; duration: 1m; cost: 0.60 USD }
  let first = await repo.read("README.md")
  let second = await repo.read("docs/architecture-map.md")
  return second
}
