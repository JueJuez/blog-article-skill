"""monitors — 持续订阅监控包。

职责：发现各平台账号的「新内容」并去重，产出待总结条目；
总结本身交给 blog-article-skill 既有管线（articles / videos）。

当前支持源：
- wechat  : 微信公众号，经 weread 代理（weread.111965.xyz）发现新文（路线 B）
- bilibili: B站UP主，经 RSSHub 发现最新视频/专栏
"""

__all__ = ["state", "wechat", "bilibili", "run"]
