"""The MCP server surface: the stdio entrypoint plus the tools it exposes.

``server.py`` is the FastMCP entrypoint (it registers the tools and runs the
stdio loop); ``context_worker.py`` is the isolated subprocess runner for the
heaviest tool. The tool bodies are split by responsibility category --
``system``, ``memory``, ``ollama``, ``editing`` -- and ``_shared`` holds the
handful of helpers common to more than one of them, so no category module has
to import another. ``cli.py`` (at the package root) imports the same category
modules to back its CLI subcommands.
"""
