"""One-tool MCP server over stdio: proves stdio discovery starts a real server, confined."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("probe")


@server.tool()
def ping() -> str:
    """Reply with pong."""
    return "pong"


if __name__ == "__main__":
    server.run("stdio")
