// `use` names a module the host resolves. With no source for it, the check cannot
// build a tree, and it says so instead of checking a program missing its helper.
module use_without_source

use helper

schema Note {
  text: String
}

workflow one() -> Note {
  budget { steps: 10 }
  return Note(text: "x")
}
