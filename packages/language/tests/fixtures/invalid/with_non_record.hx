// `with` updates a record. A String has no fields to update.
module with_non_record

workflow one() -> String {
  budget { steps: 10 }
  let text = "hello"
  return text with { text: "nope" }
}
