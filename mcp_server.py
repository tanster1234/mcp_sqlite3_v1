import sys
import sqlite3
from loguru import logger
from mcp.server.fastmcp import FastMCP

# Redirect all logs to stderr so stdout is reserved for MCP protocol
logger.remove()
logger.add(sys.stderr)

# Optional: log a startup message (stderr is safe)
print("Starting MCP server...", file=sys.stderr)

# Create an MCP server instance
mcp = FastMCP("SQLite SQL Assistant")

@mcp.tool()
def query_data(sql: str) -> str:
    """Executes raw SQL on the local SQLite database"""
    try:
        conn = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute(sql)

        rows = cursor.fetchall()
        conn.commit()

        # Return query result or a success message
        return "\n".join(str(row) for row in rows) if rows else "✅ Query ran successfully."
    except Exception as e:
        return f"❌ SQL Error: {e}"
    finally:
        conn.close()

# You can add more tools here, like schema_introspection(), get_table_names(), etc.

# Start the server
if __name__ == "__main__":
    mcp.run(transport="stdio")
