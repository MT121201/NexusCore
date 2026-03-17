#src/mcp/postgres_server.py

import asyncpg
import logging
from typing import Any, List, Dict
from mcp.server.fastmcp import FastMCP
from src.core.config import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize the MCP Server
mcp = FastMCP("Postgres-MCP-Sidecar")


async def get_db_connection():
    """Helper to get a database connection."""
    return await asyncpg.connect(settings.database_url)


@mcp.tool()
async def list_tables() -> str:
    """Returns a list of all tables in the public schema of the database."""
    logger.info("Tool called: list_tables")
    query = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'; \
            """
    try:
        conn = await get_db_connection()
        rows = await conn.fetch(query)
        await conn.close()

        tables = [row["table_name"] for row in rows]
        if not tables:
            return "No tables found in the public schema."
        return f"Found tables: {', '.join(tables)}"
    except Exception as e:
        return f"Error connecting to database: {str(e)}"


@mcp.tool()
async def describe_table(table_name: str) -> str:
    """Returns the schema (columns and data types) for a specific table."""
    logger.info(f"Tool called: describe_table for {table_name}")
    query = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = $1; \
            """
    try:
        conn = await get_db_connection()
        rows = await conn.fetch(query, table_name)
        await conn.close()

        if not rows:
            return f"Table '{table_name}' not found or has no columns."

        schema = [f"- {row['column_name']} ({row['data_type']})" for row in rows]
        return f"Schema for table '{table_name}':\n" + "\n".join(schema)
    except Exception as e:
        return f"Error retrieving schema: {str(e)}"


@mcp.tool()
async def run_read_only_query(sql_query: str) -> str:
    """
    Executes a SELECT query on the database.
    Do NOT use this for INSERT, UPDATE, or DELETE operations.
    """
    logger.info(f"Tool called: run_read_only_query executing -> {sql_query}")

    # Basic security check
    if not sql_query.strip().upper().startswith("SELECT"):
        return "Error: Only SELECT queries are allowed for safety reasons."

    try:
        conn = await get_db_connection()
        # Limit the results to prevent massive memory spikes if the AI runs `SELECT *`
        safe_query = f"SELECT * FROM ({sql_query}) AS sub LIMIT 50;"

        rows = await conn.fetch(safe_query)
        await conn.close()

        if not rows:
            return "Query executed successfully but returned 0 rows."

        # Format the output for the AI
        results = []
        for row in rows:
            results.append(str(dict(row)))

        return "Query Results (Limited to 50 rows):\n" + "\n".join(results)
    except Exception as e:
        return f"Database error during query execution: {str(e)}"


if __name__ == "__main__":
    # The MCP server communicates over standard input/output (stdio)
    # so the LangGraph agent can spin it up as a subprocess and talk to it.
    logger.info("Starting Postgres MCP Server via stdio...")
    mcp.run()