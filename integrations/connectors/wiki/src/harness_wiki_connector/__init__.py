"""Wiki connectors (local markdown tree + HTTP wiki API) and skills sync.

Notion is deliberately out of scope for now: it needs OAuth credentials and a
network account, neither of which can be validated in this environment. The
HTTP connector's contract is small enough that a Notion adapter (or any
internal-wiki adapter) is a thin translation layer on top of it.
"""

from .connector import WikiConnector, WikiError, WikiPage, slugify
from .http_wiki import HttpWikiConnector, default_url_guard
from .local_markdown import LocalMarkdownWiki
from .sync import SyncReport, WikiSkillSink, sync_wiki_to_skills

__all__ = [
    "HttpWikiConnector",
    "LocalMarkdownWiki",
    "SyncReport",
    "WikiConnector",
    "WikiError",
    "WikiPage",
    "WikiSkillSink",
    "default_url_guard",
    "slugify",
    "sync_wiki_to_skills",
]
