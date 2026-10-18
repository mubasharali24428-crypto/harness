from pathlib import Path

from harness_sandbox.policy import validate_path_edit


class DiffApplyError(Exception):
    pass


def apply_unified_diff(
    workspace_dir: Path,
    target_path: str,
    diff_text: str,
    forbidden_paths: list[str] | None = None,
) -> None:
    """Applies a unified diff to a target file within the workspace safely."""
    if not diff_text.strip():
        return

    validate_path_edit(target_path, forbidden_paths)

    # T17: realpath containment (every component, dangling links included), then
    # re-run the forbidden/secret checks on where the edit REALLY lands.
    full_path = (workspace_dir / target_path).resolve()

    try:
        real_rel = full_path.relative_to(workspace_dir.resolve())
    except ValueError as e:
        raise DiffApplyError(f"Target path '{target_path}' is outside the workspace root.") from e
    validate_path_edit(real_rel.as_posix(), forbidden_paths)

    lines = diff_text.splitlines()
    if not lines:
        raise DiffApplyError("Diff is empty.")

    if full_path.exists():
        original_content = full_path.read_text(encoding="utf-8")
        orig_lines = original_content.splitlines()
    else:
        orig_lines = []

    hunks = []
    current_hunk = None

    for line in lines:
        if line.startswith("@@"):
            if current_hunk is not None:
                hunks.append(current_hunk)
            current_hunk = {"header": line, "lines": []}
        elif current_hunk is not None:
            current_hunk["lines"].append(line)

    if current_hunk is not None:
        hunks.append(current_hunk)

    if not hunks:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(diff_text, encoding="utf-8")
        return

    result_lines = list(orig_lines)

    for hunk in hunks:
        hunk_lines = hunk["lines"]
        old_slice = []
        new_slice = []

        for h_line in hunk_lines:
            if h_line.startswith("-"):
                old_slice.append(h_line[1:])
            elif h_line.startswith("+"):
                new_slice.append(h_line[1:])
            elif h_line.startswith(" "):
                old_slice.append(h_line[1:])
                new_slice.append(h_line[1:])

        matched_idx = None
        if not old_slice:
            matched_idx = len(result_lines)
        else:
            for i in range(len(result_lines) - len(old_slice) + 1):
                if result_lines[i : i + len(old_slice)] == old_slice:
                    matched_idx = i
                    break

        if matched_idx is None:
            if not orig_lines:
                result_lines = new_slice
            else:
                raise DiffApplyError(f"Could not locate matching context hunk for '{target_path}'.")
        else:
            result_lines[matched_idx : matched_idx + len(old_slice)] = new_slice

    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text("\n".join(result_lines) + ("\n" if result_lines else ""), encoding="utf-8")


def apply_diff(original: str, diff: str):
    """String-level facade kept for simple in-memory diff application."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        target = ws / "test.txt"
        target.write_text(original)
        try:
            apply_unified_diff(ws, "test.txt", diff)
            return target.read_text()
        except DiffApplyError:
            raise
