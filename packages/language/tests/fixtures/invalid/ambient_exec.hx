fn grab() -> Bool {
  let text = exec("cat /etc/passwd")
  return text == ""
}
