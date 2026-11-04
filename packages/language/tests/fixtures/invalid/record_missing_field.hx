schema Summary {
  text: String
  path: String
}

workflow build() -> Summary {
  budget { steps: 10; tool_calls: 1; duration: 1m }
  return Summary(text: "only one field")
}
