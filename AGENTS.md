# Commit requirements

本文件仅参考 [Want-To-Be-Catgirl-Loli 的 Commit titles 章节](https://github.com/Hylfrd/Want-To-Be-Catgirl-Loli/blob/82ee57a6b3ef811276a62a5de4784af4fe3cf0f6/AGENTS.md#4-commit-titles)，不引入该仓库的其他协作、AI 记录或开发要求。

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
