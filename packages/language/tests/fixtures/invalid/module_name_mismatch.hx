// The name used and the name declared must agree: otherwise a run cannot say which
// file it executed, and two files could claim one name.
module module_name_mismatch

use notes

schema Note {
  text: String
}

workflow one() -> Note {
  budget { steps: 10 }
  return Note(text: "x")
}
