// The accumulator's declared type is checked at both ends: this block hands back
// a String where an Int was declared, so the next pass could not have used it.
module accumulate_type

workflow one() -> Int {
  budget { steps: 40 }
  let words = ["a", "b"]
  for word in words accumulate total: Int = 0 {
    word
  }
  return total
}
