// M8's two ways to compute a value, in one file: `with` builds a new record and
// `accumulate` binds one per loop pass. Neither is an assignment, so the loop's
// termination story is still "the list bounds the passes, the budget bounds the
// spend" and no value anywhere is edited in place.
module update_and_accumulate

schema Note {
  text: String
  done: Bool
}

workflow build() -> String {
  budget { steps: 40 }
  let first = Note(text: "one", done: false)
  let second = first with { text: "two", done: true }
  ensure first.done == false
  ensure second.done == true
  for note in [first, second] accumulate body: String = "" {
    body + note.text
  }
  return body
}
