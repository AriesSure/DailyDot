"""Render untrusted report Markdown with a restricted Python-Markdown parser."""

from html import unescape
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError, SubElement, fromstring

from markdown import Markdown
from markdown.treeprocessors import Treeprocessor
from markdown.util import HTML_PLACEHOLDER_RE
from markupsafe import Markup, escape


_REPORT_TAGS = frozenset({
    "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "br", "blockquote",
    "strong", "em", "ol", "ul", "li", "pre", "code", "a",
    "table", "thead", "tbody", "tr", "th", "td",
})


def _allowed_link(href: str) -> bool:
    if not href.lower().startswith(("https://", "http://")):
        return False
    decoded = unescape(href)
    if "\\" in decoded or any(ord(char) <= 32 or ord(char) == 127 for char in decoded):
        return False
    try:
        url = urlsplit(decoded)
        return bool(url.hostname and url.username is None and url.password is None)
    except ValueError:
        return False


class _ReportTree(Treeprocessor):
    def run(self, root):
        for element in root.iter():
            if element.tag not in _REPORT_TAGS:
                raise ValueError("unsupported report element")
            href = element.get("href", "")
            start = element.get("start")
            element.attrib.clear()
            if element.tag == "a":
                if _allowed_link(href):
                    element.set("href", href)
                    element.set("rel", "noopener noreferrer")
                else:
                    element.tag = "span"
            elif element.tag == "ol" and start and start.isascii() and start.isdecimal():
                element.set("start", start)

        # Only fenced_code writes to the stash. Restore standalone fences as
        # text nodes, without re-inserting stashed HTML into an attribute.
        for block in root.iter("p"):
            if not len(block) and (match := HTML_PLACEHOLDER_RE.fullmatch(block.text or "")):
                fragment = self.md.htmlStash.rawHtmlBlocks[int(match[1])]
                code = fromstring(fragment).findtext("code", default="")
                block.tag = "pre"
                block.text = None
                # Markdown's serializer preserves entities. Keep literal ones in code.
                SubElement(block, "code").text = code.replace("&", "&amp;")


def render_report_markdown(source: str) -> Markup:
    parser = Markdown(extensions=["fenced_code", "tables"])
    parser.preprocessors.deregister("html_block")
    for pattern in ("html", "entity", "automail", "image_link", "image_reference", "short_image_ref"):
        parser.inlinePatterns.deregister(pattern)
    parser.postprocessors.deregister("raw_html")
    parser.treeprocessors.register(_ReportTree(parser), "report_tree", -10)
    try:
        return Markup(parser.convert(source))
    except (IndexError, ParseError, ValueError):
        return escape(source)
