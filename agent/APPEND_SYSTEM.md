# Search tool preference

Use `ripgrep` (`rg`) instead of `grep` or `find` for searching code/text. It's recursive by default, respects `.gitignore`, and is on your PATH.

- `find . -name '*.ts'` → `rg --files -g '*.ts'`
- `grep -rl "foo"`     → `rg -l "foo"`
- Content search is just `rg "pattern"` (regex by default; `-S` smart-case, `-i`, `-A/-B`, `-t py` available).
- `grep`/`find` are still fine as a pipe filter (`| grep -v …`) or in shell scripts.
