import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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


def _parse_timestamp(value: str | None) -> datetime | None:
    """把库里两种时间格式统一解析成带时区的 datetime。

    本库写入的时间是 ISO-8601（带 +00:00），而 created_at 的默认值是 SQLite 的
    CURRENT_TIMESTAMP（`YYYY-MM-DD HH:MM:SS`，无时区）。要按「最近 N 天」过滤，
    两种都得能解析，否则老行会被误判成「很久以前」或「在未来」。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    other_item_id INTEGER NOT NULL,
                    similarity REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(item_id, other_item_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_semantic_links_other
                ON semantic_links(other_item_id)
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

    def recent_titles(
        self,
        exclude_id: int,
        limit: int,
        days: int,
    ) -> list[tuple[int, str]]:
        """返回「最近 N 天、最多 limit 条」的 (id, title)，供语义归并比较。

        为什么按 last_seen_at 排序：同一件事被反复采到，last_seen_at 才是它
        「最近还在被讨论」的时间，而不是它第一次入库的 created_at。
        """
        cutoff = datetime.now(tz=UTC) - timedelta(days=days)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, last_seen_at, created_at
                FROM radar_items
                WHERE id != ?
                ORDER BY COALESCE(last_seen_at, created_at) DESC
                LIMIT ?
                """,
                (exclude_id, limit),
            ).fetchall()

        result: list[tuple[int, str]] = []
        for row in rows:
            # 在 Python 侧过滤时间窗口：SQL 字符串比较对两种时间格式不可靠。
            seen = _parse_timestamp(row["last_seen_at"] or row["created_at"])
            if seen is None or seen >= cutoff:
                result.append((int(row["id"]), str(row["title"])))
        return result

    def record_semantic_links(self, candidates: list[tuple[int, int, float]]) -> None:
        """把「疑似同源候选」配对连同相似度写进库，供将来标定阈值。

        只存相似度数值和两端的条目 id，不存向量 —— 向量会让库体积失控，
        而标定阈值只需要「谁和谁、有多像」这两个事实。
        同一对重复出现时覆盖更新：同一对在不同轮次算出的相似度可能因标题更新而变化。
        """
        if not candidates:
            return
        now = _now()
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO semantic_links (item_id, other_item_id, similarity, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(item_id, other_item_id) DO UPDATE SET
                    similarity = excluded.similarity,
                    created_at = excluded.created_at
                """,
                [
                    (item_id, other_id, round(float(similarity), 6), now)
                    for item_id, other_id, similarity in candidates
                ],
            )

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
            SELECT id, source, external_id, title, url, published_at,
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
            peer_counts = self._semantic_peer_counts(
                connection, [int(row["id"]) for row in rows]
            )
        return [
            self._with_derived_metrics(item, row, corroboration, peer_counts)
            for item, row in zip(items, rows, strict=True)
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
    def _with_derived_metrics(
        item: RadarItem,
        row: sqlite3.Row,
        corroboration: dict[str, list[str]],
        peer_counts: dict[int, int],
    ) -> RadarItem:
        """把读取时才算得出来的派生信号并进 metrics。

        两个派生信号都不落库（等级是算出来的，候选配对也只存数值）：
          corroborating_sources —— 还有哪些源提到同一个东西（精确 dedup_key 命中）；
          semantic_peer_count   —— 疑似同源候选数（任务 1 记下的语义配对，按条目聚合）。
        """
        extra: dict[str, Any] = {}
        ref = article_ref(item.url)
        others = sorted(set(corroboration.get(ref.key, [])) - {item.source})
        if others:
            extra["corroborating_sources"] = others
        peer_count = peer_counts.get(int(row["id"]), 0)
        if peer_count > 0:
            extra["semantic_peer_count"] = peer_count
        if not extra:
            return item
        metrics = dict(item.metrics or {})
        metrics.update(extra)
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

    @staticmethod
    def _semantic_peer_counts(
        connection: sqlite3.Connection, item_ids: list[int]
    ) -> dict[int, int]:
        """一次查清每个条目有多少个疑似同源候选。

        配对是「新条目 -> 旧条目」的有向记录，所以聚合时要两个方向都看：
        该条目既可能是 item_id（较新一侧），也可能是 other_item_id（较旧一侧）。
        """
        if not item_ids:
            return {}
        placeholders = ",".join("?" for _ in item_ids)
        rows = connection.execute(
            f"""
            SELECT x, COUNT(DISTINCT y) AS cnt FROM (
                SELECT item_id AS x, other_item_id AS y
                FROM semantic_links
                WHERE item_id IN ({placeholders})
                UNION ALL
                SELECT other_item_id AS x, item_id AS y
                FROM semantic_links
                WHERE other_item_id IN ({placeholders})
            )
            GROUP BY x
            """,
            item_ids + item_ids,
        ).fetchall()
        return {int(row["x"]): int(row["cnt"]) for row in rows}

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
