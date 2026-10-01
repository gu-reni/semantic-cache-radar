import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from radar.app.models import RadarItem
from radar.app.normalize import article_ref

# 各源自己的「动量」数字。故意不从跨源统一成一个分数：
# HN 的点数、V2EX 的回复数、GitHub 的今日新增星数，量纲和分布都不一样，
# 硬凑成一个 0-100 的「热度」等于凭空造一个无法解释的数。展示层要横向比时，
# 按「在本源里排第几名」来比（见 list_items 的 sort="heat"），那才是可解释的。
HEAT_FIELD_BY_SOURCE: dict[str, str] = {
    "hackernews": "points",
    "v2ex": "replies",
    "github-trending": "stars_today",
}

# 老库升级用：列名 -> 建表时的类型。
_ADDED_COLUMNS: dict[str, str] = {
    "metrics_json": "TEXT",
    "dedup_key": "TEXT",
    "first_seen_at": "TEXT",
    "last_seen_at": "TEXT",
}


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


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
            self._migrate(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS radar_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    dedup_key TEXT,
                    seen_at TEXT NOT NULL,
                    metrics_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_radar_snapshots_key
                ON radar_snapshots(dedup_key, seen_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_radar_items_dedup_key
                ON radar_items(dedup_key)
                """
            )

    def _migrate(self, connection: sqlite3.Connection) -> None:
        """给已存在的库补列，并回填 dedup_key。

        线上那台 ECS 上已经躺着一个有真实数据的库，重建它会丢掉 30 条已采数据。
        新列只加不删；dedup_key 是后来才有的概念，历史行必须回填，
        否则归并查询会把它们全部当成「无归属」而漏掉。
        """
        existing = {
            row["name"] for row in connection.execute("PRAGMA table_info(radar_items)")
        }
        for column, column_type in _ADDED_COLUMNS.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE radar_items ADD COLUMN {column} {column_type}")

        stale = connection.execute(
            "SELECT id, url FROM radar_items WHERE dedup_key IS NULL OR dedup_key = ''"
        ).fetchall()
        if stale:
            connection.executemany(
                "UPDATE radar_items SET dedup_key = ? WHERE id = ?",
                [(article_ref(row["url"]).key, row["id"]) for row in stale],
            )

    def upsert(self, item: RadarItem) -> int:
        """写入一条资讯，并记一次快照。

        快照是这个表存在的理由：radar_items 上的 UNIQUE(source, external_id) 是
        覆盖式更新，只留得下「现在什么样」。而雷达的价值在于「在动」——
        涨得快不快、什么时候第一次出现、是不是在退热，全都需要时间序列。
        所以每次 upsert 顺手往 radar_snapshots 追加一行，只增不改。
        """
        now = _now()
        ref = article_ref(item.url)
        metrics_json = json.dumps(item.metrics, ensure_ascii=False) if item.metrics else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO radar_items (
                    source, external_id, title, url, published_at, summary, tags_json,
                    metrics_json, dedup_key, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    published_at = excluded.published_at,
                    summary = excluded.summary,
                    tags_json = excluded.tags_json,
                    metrics_json = excluded.metrics_json,
                    dedup_key = excluded.dedup_key,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    item.source,
                    item.external_id,
                    item.title,
                    item.url,
                    item.published_at,
                    item.summary,
                    json.dumps(item.tags or [], ensure_ascii=False),
                    metrics_json,
                    ref.key,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO radar_snapshots (
                    source, external_id, dedup_key, seen_at, metrics_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (item.source, item.external_id, ref.key, now, metrics_json),
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
        sort: str = "time",
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
        if sort == "heat":
            # 按「在本源里的排名」排，而不是把不同量纲的数字直接比大小：
            # GitHub 的 1,179 个今日新星和 HN 的 153 点不是一回事，
            # 直接比大小会恒定偏向量级大的那个源。「源内第几名」才是可解释的。
            heat_case = " ".join(
                f"WHEN '{name}' THEN json_extract(metrics_json, '$.{field}')"
                for name, field in sorted(HEAT_FIELD_BY_SOURCE.items())
            )
            select_rank = f""",
                   ROW_NUMBER() OVER (
                       PARTITION BY source
                       ORDER BY COALESCE(CASE source {heat_case} ELSE NULL END, -1) DESC,
                                COALESCE(published_at, created_at) DESC
                   ) AS heat_rank"""
            order_clause = "ORDER BY heat_rank ASC, COALESCE(published_at, created_at) DESC"
        else:
            # 时间排序下也照样选出这个列，让 row["heat_rank"] 永远存在，
            # 免得调用方得先判断列在不在。
            select_rank = ", NULL AS heat_rank"
            order_clause = "ORDER BY COALESCE(published_at, created_at) DESC"

        query = f"""
            SELECT source, external_id, title, url, published_at,
                   summary, tags_json, created_at, metrics_json,
                   dedup_key, first_seen_at, last_seen_at
                   {select_rank}
            FROM radar_items
            {where_clause}
            {order_clause}
            LIMIT ?
        """
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
            items = [self._row_to_item(row) for row in rows]
            corroboration = self._corroboration_for(connection, rows)
        return [
            self._with_corroboration(item, corroboration)
            for item in items
        ]

    @staticmethod
    def _corroboration_for(
        connection: sqlite3.Connection, rows: list[sqlite3.Row]
    ) -> dict[str, list[str]]:
        """每个 dedup_key 都有哪些源提到过。

        「同一个东西被多个源同时提到」是比任何单一指标都硬的信号，
        但它只在跨行看的时候才存在。这里一次性查出来，避免逐条查询。
        """
        keys = sorted({row["dedup_key"] for row in rows if row["dedup_key"]})
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        found = connection.execute(
            f"""
            SELECT dedup_key, source FROM radar_items
            WHERE dedup_key IN ({placeholders})
            GROUP BY dedup_key, source
            """,
            keys,
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        for row in found:
            grouped.setdefault(row["dedup_key"], []).append(row["source"])
        return grouped

    @staticmethod
    def _with_corroboration(item: RadarItem, grouped: dict[str, list[str]]) -> RadarItem:
        ref = article_ref(item.url)
        others = sorted(set(grouped.get(ref.key, [])) - {item.source})
        if not others:
            return item
        metrics = dict(item.metrics or {})
        metrics["corroborating_sources"] = others
        return RadarItem(
            source=item.source,
            external_id=item.external_id,
            title=item.title,
            url=item.url,
            published_at=item.published_at,
            summary=item.summary,
            tags=item.tags,
            created_at=item.created_at,
            metrics=metrics,
        )

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM radar_items").fetchone()
        assert row is not None
        return int(row["count"])

    def snapshot_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM radar_snapshots"
            ).fetchone()
        assert row is not None
        return int(row["count"])

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> RadarItem:
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        # heat_rank 是本库算出来的（源内按动量的名次），与 metrics 里
        # 那些源站原值分开标注，免得读的人以为它也是抓来的。
        #
        # 这里必须写 .keys()，不能听 ruff SIM118 的话简写成 `in row`：
        # sqlite3.Row 没有实现 __contains__，`in` 会退化成遍历「值」，
        # 于是 "heat_rank" in row 恒为 False，名次静默全部丢失。
        # 已实测确认（'alpha' in row 为 False，而 'alpha' in row.keys() 为 True）。
        heat_rank = row["heat_rank"] if "heat_rank" in row.keys() else None  # noqa: SIM118
        if heat_rank is not None:
            metrics["heat_rank_in_source"] = int(heat_rank)
        return RadarItem(
            source=row["source"],
            external_id=row["external_id"],
            title=row["title"],
            url=row["url"],
            published_at=row["published_at"],
            summary=row["summary"],
            tags=json.loads(row["tags_json"] or "[]"),
            created_at=row["created_at"],
            metrics=metrics or None,
        )
