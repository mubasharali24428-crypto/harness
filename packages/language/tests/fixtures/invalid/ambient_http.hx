schema RepoFile {
  path: String
  text: String
}

workflow fetch(url: String) -> RepoFile {
  requires repo.read
  budget { steps: 5 }

  let page = await http.get(url)
  return page
}
