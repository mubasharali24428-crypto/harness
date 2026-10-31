// A binding may not reuse a name the file already declares: one name, one meaning.
module binding_shadows_declaration

schema Note {
  text: String
}

fn shout(note: Note) -> Note {
  return note with { text: "!" }
}

workflow one() -> Note {
  budget { steps: 10 }
  let Note = 1
  return shout(Note(text: "x"))
}
