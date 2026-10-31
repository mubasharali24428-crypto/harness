fn grant_myself() -> Bool {
  let ok = policy.grant("repo.write")
  return ok
}
