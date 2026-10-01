"""把「不同来源指向同一个东西」的条目认出来。

为什么需要这一层：
  HN 上的帖子经常直接指向 GitHub 仓库，而同一个仓库也可能同时出现在
  GitHub Trending 里 —— 两条记录说的其实是同一件事，却分属两个源、各算一条。
  「同一个东西被多个源同时提到」是比任何单一指标都硬的信号（多源互证），
  但现在这个信号被结构性地丢掉了：看不出来两条记录有关系。

这一层只做「认出它们是一回事」，不做删除：
  归并后的分组只在读取时计算（见 RadarRepository.list_items 的
  corroborating_sources），原始行一条都不合并，避免掉进「去重去出信息缺失」的坑。
"""

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 常见的营销/统计参数：同一篇文章带上不同的 utm 值就该被视为同一个 URL。
_TRACKING_PREFIXES = ("utm_", "ref_", "mc_", "pk_")
_TRACKING_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "igshid",
        "spm",
        "share_token",
        "from",
        "source",
        "ref",
        "share_source",
    }
)

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


@dataclass(frozen=True)
class ArticleRef:
    """一条资讯「讲的是哪个东西」。"""

    key: str
    """归并用的主键。同 key 即视为同一个东西。"""

    kind: str
    """`github_repo` 或 `web_page`。前者才可能跨源撞上 GitHub Trending。"""

    label: str
    """给人看的名字，例如 `owner/repo` 或 `example.com/some/path`。"""


def _strip_tracking(query: str) -> str:
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key.lower() not in _TRACKING_KEYS
        and not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlencode(kept)


def normalize_url(url: str) -> str:
    """把 URL 归一化到「同一个资源只有一个写法」。

    去掉 fragment、去尾斜杠、小写主机名、剔除营销参数。
    留着 path 与其余查询串 —— 那是身份的一部分，删了会把不同页面并成一个。
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if not host:
        return url.strip().lower()
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (parsed.scheme.lower() or "https", netloc, path, "", _strip_tracking(parsed.query), "")
    )


def github_repo_slug(url: str) -> str | None:
    """URL 指向 GitHub 仓库时返回 `owner/repo`（小写），否则 None。

    只认仓库本身：`/owner/repo` 及其子路径（issues、blob、tree…）都归到同一个
    owner/repo。GitHub Trending 的条目 URL 是 `https://github.com/owner/repo`，
    而 HN 上那个帖子的 URL 往往指向同一个仓库里的某个文件 —— 两者必须撞上。
    """
    parsed = urlparse(url.strip())
    if (parsed.hostname or "").lower() not in _GITHUB_HOSTS:
        return None
    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) < 2:
        return None
    owner, repo = parts[0].lower(), parts[1].lower()
    # 这些是 GitHub 自己的功能页，不是仓库：/orgs/x、/topics/y 之类。
    if owner in {"orgs", "topics", "collections", "sponsors", "settings", "features"}:
        return None
    return f"{owner}/{repo}"


def article_ref(url: str) -> ArticleRef:
    """由 URL 得到归并主键。"""
    slug = github_repo_slug(url)
    if slug:
        return ArticleRef(key=f"github:{slug}", kind="github_repo", label=slug)

    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    label = f"{parsed.netloc}{parsed.path}".rstrip("/")
    return ArticleRef(key=f"url:{normalized}", kind="web_page", label=label)
