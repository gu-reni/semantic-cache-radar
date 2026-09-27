import json
import sqlite3
from pathlib import Path

from radar.app.models import RadarItem


class RadarRepository:
    """Persist radar items in a small SQLite database."""

    def __init__(self, database_path: str) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._database_path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS radar_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT,
                    summary TEXT,
                    tags_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, external_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_radar_items_published_at
                ON radar_items(published_at)
                """
            )

    def upsert(self, item: RadarItem) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO radar_items (
                    source, external_id, title, url, published_at, summary, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    published_at = excluded.published_at,
                    summary = excluded.summary,
                    tags_json = excluded.tags_json
                """,
                (
                    item.source,
                    item.external_id,
                    item.title,
                    item.url,
                    item.published_at,
                    item.summary,
                    json.dumps(item.tags or [], ensure_ascii=False),
                ),
            )
            row = connection.execute(
                "SELECT id FROM radar_items WHERE source = ? AND external_id = ?",
                (item.source, item.external_id),
            ).fetchone()
            assert row is not None
            return int(row["id"])

    def list_items(
        self,
        limit: int = 20,
        source: str | None = None,
        keyword: str | None = None,
    ) -> list[RadarItem]:
        conditions: list[str] = []
        parameters: list[str | int] = []
        if source:
            conditions.append("source = ?")
            parameters.append(source)
        if keyword:
            conditions.append("title LIKE ?")
            parameters.append(f"%{keyword}%")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT source, external_id, title, url, published_at,
                   summary, tags_json, created_at
            FROM radar_items
            {where_clause}
            ORDER BY COALESCE(published_at, created_at) DESC
            LIMIT ?
        """
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_item(row) for row in rows]

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM radar_items").fetchone()
        assert row is not None
        return int(row["count"])

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> RadarItem:
        return RadarItem(
            source=row["source"],
            external_id=row["external_id"],
            title=row["title"],
            url=row["url"],
            published_at=row["published_at"],
            summary=row["summary"],
            tags=json.loads(row["tags_json"] or "[]"),
            created_at=row["created_at"],
        )
