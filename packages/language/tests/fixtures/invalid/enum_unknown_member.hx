enum Severity {
  low
  high
}

workflow pick() -> Severity {
  budget { steps: 10; tool_calls: 1; duration: 1m }
  return Severity.urgent
}
