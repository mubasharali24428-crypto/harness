// A record update may name a field the record has, and only once.
module with_unknown_field

schema Note {
  text: String
}

workflow one() -> Note {
  budget { steps: 10 }
  return Note(text: "x") with { other: "y", text: "z", text: "w" }
}
