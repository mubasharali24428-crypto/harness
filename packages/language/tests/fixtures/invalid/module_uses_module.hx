// One level deep, on purpose: a helper that pulls in helpers needs a re-export and
// shadowing story this language does not have.
module module_uses_module

use notes

schema Note {
  text: String
}

workflow one() -> Note {
  budget { steps: 10 }
  return Note(text: "x")
}
