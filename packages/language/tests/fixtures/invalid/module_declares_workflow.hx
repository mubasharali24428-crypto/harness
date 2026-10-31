// A module exports types and pure functions. A workflow is an entry point, and two
// entry points in one program is not a decision the language makes by itself.
module module_declares_workflow

use notes

schema Note {
  text: String
}

workflow one() -> Note {
  budget { steps: 10 }
  return Note(text: "x")
}
