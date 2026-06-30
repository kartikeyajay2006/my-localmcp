# neo-localmcp Claude Code commands

Installed commands live under `/neo-localmcp:*`.

Recommended first move in large repos:

```text
/neo-localmcp:context debug your task: KnownSymbol, FileName.cs
```

Claude should ask naturally, but include known symbols/files when possible. `neo-localmcp` normalizes the query, ranks source files by intent, and returns file/line guidance. Claude still verifies current source and produces exact patches.
