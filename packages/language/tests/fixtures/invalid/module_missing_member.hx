// `notes.Helper` is not declared by the module, so the name is refused by module and
// by name rather than resolving to something nearby.
module module_missing_member

use notes

schema Note {
  text: String
}

workflow one() -> Note {
  budget { steps: 10 }
  return Note(text: notes.Missing.text)
}
