// The core profile: pure, deterministic, no effects and no workflows.
module helpers

schema Ticket {
  id: String
  title: String
  resolved: Bool
}

enum Severity {
  low
  medium
  high
}

fn is_open(ticket: Ticket) -> Bool {
  return not ticket.resolved
}

fn matches(ticket: Ticket, title: String) -> Bool {
  return ticket.title == title
}

fn describe(severity: Severity) -> String {
  return "severity"
}
