workflow scan() -> Int {
  budget { steps: 10; tool_calls: 3; duration: 1m }
  let name = "docs/"
  for part in name {
    ensure part != ""
  }
  return 0
}
