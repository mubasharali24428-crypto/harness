// Which files a program is built from is answered at the top of it.
module use_after_declarations

schema Note {
  text: String
}

use helper

workflow one() -> Note {
  budget { steps: 10 }
  return Note(text: "x")
}
