from dataclasses import dataclass


@dataclass(frozen=True)
class RadarItem:
    source: str
    external_id: str
    title: str
    url: str
    published_at: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    created_at: str | None = None
