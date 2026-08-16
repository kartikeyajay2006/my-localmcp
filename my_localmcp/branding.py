from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Identity:
    product_name: str = "my-localmcp"
    display_name: str = "my-localmcp"
    cli_name: str = "my-localmcp"
    slash_prefix: str = "my-localmcp"
    mcp_server_name: str = "my-localmcp"
    package_name: str = "my-localmcp"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


IDENTITY = Identity()
