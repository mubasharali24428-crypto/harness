# Review conventions

- A review names the behaviour a diff changes, not the lines it touches.
- Refuse a change that adds a silent default to a public function; ask for an
  explicit argument instead.
- Every review ends with one of: approve, request changes (with the reason), or
  "needs a human decision" naming the owner.
- Never merge: merging is the human's action, on every tier.
