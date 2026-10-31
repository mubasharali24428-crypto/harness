// A qualified name needs the `use` that brings it in; there is no global namespace
// to reach into.
module qualified_without_use

schema Note {
  text: String
}

workflow one() -> helper.Note {
  budget { steps: 10 }
  return Note(text: "x")
}
