schema Issue {
  id: String
}

fn broken(input: Issue) -> Bool {
  let = 3
  return true
}
