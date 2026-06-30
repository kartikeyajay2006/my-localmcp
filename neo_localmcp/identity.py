from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Identity:
    product_name: str = "neo-localmcp"
    display_name: str = "neo-localmcp"
    cli_name: str = "neo-localmcp"
    slash_prefix: str = "neo-localmcp"
    mcp_server_name: str = "neo-localmcp"
    package_name: str = "neo-localmcp"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


IDENTITY = Identity()
