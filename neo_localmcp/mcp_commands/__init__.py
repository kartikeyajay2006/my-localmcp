"""MCP tool implementations, split by responsibility category.

``server.py`` and ``cli.py`` import the category modules directly
(``system``, ``memory``, ``ollama``, ``editing``); ``_shared`` holds the
handful of helpers common to more than one of them, so no category module
has to import another.
"""
