# Commit requirements

- 使用 Angular 风格标题：`<type>(<scope>): <summary>`。
- 每次提交仅包含标题，不添加正文或 footer；不采用 Angular 的强制正文要求。
- `type` 使用 `build`、`ci`、`docs`、`feat`、`fix`、`perf`、`refactor`、`test`；撤销提交使用 `revert`。
- 必须包含有实际意义的 `scope`，按受影响的项目区域命名；本文件的提交规范变更使用 `ai-policy`。
- `summary` 使用清晰英文，以小写祈使动词开头，末尾不加句号。可以使用简短句子，不必压缩成过短短语，也不要写成长段落。

示例：

```text
docs(ai-policy): define commit title requirements
docs(project): initialize repository documentation
feat(ttlock): fetch locks from the cloud API
fix(auth): refresh expired access tokens
```
