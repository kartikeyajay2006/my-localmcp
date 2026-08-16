# my-localmcp Claude Code commands

Installed commands live under `/my-localmcp:*`.

Recommended first move in large repos:

```text
/my-localmcp:context debug your task: KnownSymbol, FileName.cs
```

Claude should ask naturally, but include known symbols/files when possible. `my-localmcp` normalizes the query, ranks source files by intent, and returns file/line guidance. Claude still verifies current source and produces exact patches.
