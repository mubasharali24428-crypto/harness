"""A4-S3: issue intake (GitHub) and issue/MR integration (GitLab).

Webhook text is untrusted context: it can only create a *pending* intake record that a human
must approve. Nothing here executes, merges, or creates a run by itself.
"""
