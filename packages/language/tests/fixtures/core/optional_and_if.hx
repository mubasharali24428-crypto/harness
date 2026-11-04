// Optional types, lists, maps and if/else returns — the pure core, exercised.
module shapes

schema Ticket {
  id: String
  labels: List<String>
  owner: String?
  meta: Map<String, String>
}

fn label_count(ticket: Ticket) -> Int {
  if ticket.owner == "" {
    return 0
  } else {
    return 1
  }
}

fn has_owner(ticket: Ticket) -> Bool {
  return ticket.owner != ""
}
