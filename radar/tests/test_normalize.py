from radar.app.normalize import article_ref, github_repo_slug, normalize_url


def test_github_repo_subpath_folds_into_the_same_article() -> None:
    """仓库里的具体文件与仓库主页必须归到同一个 key。

    这是整套跨源归并的立足点：HN 上的帖子常常直接指向仓库里的某个文件
    （/owner/repo/blob/main/x.py），而 GitHub Trending 的条目指向
    https://github.com/owner/repo —— 两者讲的是同一个项目，
    如果归不到一起，「多源互证」这个信号就永远出不来。
    """
    home = article_ref("https://github.com/psf/requests")
    blob = article_ref("https://github.com/psf/requests/blob/main/README.md")
    issues = article_ref("https://github.com/psf/requests/issues/1234")

    assert home.key == blob.key == issues.key == "github:psf/requests"
    assert home.kind == "github_repo"
    assert home.label == "psf/requests"


def test_different_repositories_stay_separate() -> None:
    a = article_ref("https://github.com/psf/requests")
    b = article_ref("https://github.com/psf/urllib3")
    assert a.key != b.key


def test_github_org_and_topic_pages_are_not_repositories() -> None:
    """GitHub 自己的功能页不是仓库，别把 /orgs/foo 认成 owner=orgs 的仓库。"""
    assert github_repo_slug("https://github.com/orgs/psf") is None
    assert github_repo_slug("https://github.com/topics/python") is None
    assert github_repo_slug("https://github.com") is None
    assert github_repo_slug("https://github.com/psf") is None


def test_tracking_parameters_do_not_create_different_articles() -> None:
    """同一篇文章带不同 utm 参数仍然是同一篇。

    不处理的话，同一篇文章被转贴时每带一次分享参数就多算一条，
    而它们看起来还是「两个不同来源在讲不同的事」。
    """
    plain = article_ref("https://example.com/blog/hello")
    tagged = article_ref("https://example.com/blog/hello?utm_source=x&utm_campaign=y&fbclid=z")
    assert plain.key == tagged.key


def test_trailing_slash_fragment_and_host_case_are_normalized() -> None:
    a = article_ref("https://Example.com/blog/hello/")
    b = article_ref("https://example.com/blog/hello#section-2")
    assert a.key == b.key


def test_meaningful_query_parameters_are_kept() -> None:
    """有身份的查询参数不能删。

    HN 的条目 URL 就是 https://news.ycombinator.com/item?id=123 ——
    把 id 当营销参数删掉，所有 HN 条目会并成同一条。
    """
    a = article_ref("https://news.ycombinator.com/item?id=123")
    b = article_ref("https://news.ycombinator.com/item?id=456")
    assert a.key != b.key
    assert "id=123" in a.key


def test_non_github_pages_keyed_by_full_url() -> None:
    ref = article_ref("https://www.v2ex.com/t/1071234")
    assert ref.kind == "web_page"
    assert ref.key == "url:https://www.v2ex.com/t/1071234"
    assert ref.label == "www.v2ex.com/t/1071234"


def test_normalize_url_returns_something_for_unparsable_input() -> None:
    """拿不到主机名也不能抛异常 —— 采集到怪 URL 不该让整次采集挂掉。"""
    assert normalize_url("not a url") == "not a url"
