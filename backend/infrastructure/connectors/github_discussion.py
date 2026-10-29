"""Select GitHub Discussion title and opening post at the HTML boundary."""
from html import escape
from html.parser import HTMLParser
from typing import cast


_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def extract_discussion(page: str) -> tuple[str, str]:
    parser = _DiscussionParser()
    parser.feed(page)
    parser.close()
    title = " ".join("".join(parser.title).split())
    if not title or not parser.body:
        raise RuntimeError("github_discussion_body_or_title_missing")
    return title, "".join(parser.body)


class _DiscussionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.body: list[str] = []
        self._stack: list[str] = []
        self._title_depth: int | None = None
        self._body_depth: int | None = None
        self._discussion_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if "js-discussion" in classes:
            self._discussion_depth = len(self._stack)
        if "js-issue-title" in classes and not self.title:
            self._title_depth = len(self._stack)
        if "comment-body" in classes and self._discussion_depth is not None and not self.body:
            self._body_depth = len(self._stack)
        if self._body_depth is not None:
            self.body.append(cast(str, self.get_starttag_text()))
        if tag not in _VOID_TAGS:
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._stack:
            return
        # GitHub HTML can contain unmatched closing tags around templates.
        depth = len(self._stack) - 1 - self._stack[::-1].index(tag)
        if self._body_depth is not None:
            self.body.append(f"</{tag}>")
            if depth <= self._body_depth:
                self._body_depth = None
        if self._title_depth is not None and depth <= self._title_depth:
            self._title_depth = None
        if self._discussion_depth is not None and depth <= self._discussion_depth:
            self._discussion_depth = None
        del self._stack[depth:]

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._title_depth is not None:
            self.title.append(data)
        if self._body_depth is not None:
            self.body.append(escape(data))


def without_form_inputs(fragment: str) -> str:
    """GitHub renders markdown task checkboxes as void input elements."""
    parser = _InputFilter()
    parser.feed(fragment)
    parser.close()
    return "".join(parser.parts)


class _InputFilter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            self.parts.append(cast(str, self.get_starttag_text()))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag != "input":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data))
