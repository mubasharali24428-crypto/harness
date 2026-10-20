# harness-wiki-connector

Pull wiki pages into the Harness skills registry as versioned skills. Ships two
connectors behind one tiny protocol, plus an idempotent sync.

| Connector | Source | Notes |
|---|---|---|
| `LocalMarkdownWiki` | a directory tree of `*.md` files | page id = relative path without suffix; title = first `# ` heading |
| `HttpWikiConnector` | any wiki with the JSON API below | page ids may be hierarchical (`eng/deploys`) |

Notion is **not** implemented: it requires OAuth credentials and a live
account, neither verifiable in this environment. Its API is a thin translation
layer over the HTTP connector contract — see “Adding a connector”.

## HTTP wiki API contract

```http
GET {base}/pages          -> {"pages": [{"id", "title", "updatedAt"}]}
GET {base}/pages/{id}     -> {"id", "title", "content", "updatedAt"}
```

Pages are fetched by id; listings may omit `content`. The connector sends
`Authorization: Bearer <token>` when a token is given.

## Usage

```python
from harness_extensions.client import ExtensionsClient
from harness_wiki_connector import HttpWikiConnector, LocalMarkdownWiki, sync_wiki_to_skills

async with ExtensionsClient("https://harness.internal", token=os.environ["HARNESS_TOKEN"]) as c:
    report = await sync_wiki_to_skills(c, LocalMarkdownWiki("./wiki"))
    print(report.counts)  # {"created": 12, "updated": 1, "unchanged": 40}
```

Sync semantics:

- skill name = `{namespace}/{slug(pageId)}` (default namespace `wiki`);
- content change -> **new skill version** (registry keeps history; nothing is
  edited in place);
- unchanged pages are left untouched; the sync is safe to run on a schedule.

## URL vetting

`HttpWikiConnector` validates its base URL before any request. Standalone, the
default guard allows only `http`/`https`. Inside the Harness extensions service,
inject the service URL policy so private/loopback hosts are refused unless the
operator opted in:

```python
from harness_extensions.url_policy import validate_server_url

HttpWikiConnector(url, url_guard=lambda u: validate_server_url(u, allow_private_hosts=False))
```

## Adding a connector (e.g. Notion)

Implement two methods — `list_pages()` and `get_page(page_id)` returning
`WikiPage(pageId, title, content, updatedAt)` — and `sync_wiki_to_skills` works
unchanged. No registry or SDK changes needed.

## Development

```sh
./packages/extensions/.venv/bin/python -m pytest integrations/connectors/wiki/tests -q
```
