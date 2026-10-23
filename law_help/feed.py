"""RSS 2.0 feeds of a search (GET /feed), so a feed reader shows each day's new matching judgments.

api.feed runs the search; this module only writes the XML.
"""

from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET

FEED_SIZE = 50
ATOM_NS = "http://www.w3.org/2005/Atom"

# How each search parameter reads in the feed's title.
_LABELS = {"judge": "judge", "act": "act", "section": "s.", "case_type": "case type", "court": "court",
           "bench": "bench", "disposal": "outcome", "decided_from": "decided from", "decided_to": "decided to"}


def feed_title(params: dict) -> str:
    """'law_help: "bail" · act NDPS · bench jodhpur', or 'law_help: new judgments' for no search."""
    parts = [f'"{params["q"]}"'] if params.get("q") else []
    parts += [f"{label} {params[k]}" for k, label in _LABELS.items() if params.get(k)]
    if params.get("landmark") in ("true", "1", "yes", "on"):
        parts.append("landmarks")
    return "law_help: " + (" · ".join(parts) if parts else "new judgments")


def render(title: str, link: str, self_link: str, items: list[dict]) -> bytes:
    """items: dicts with title, link, description, guid and added (a datetime)."""
    ET.register_namespace("atom", ATOM_NS)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = link
    ET.SubElement(channel, "description").text = (
        f"The newest {FEED_SIZE} judgments added to law_help that match this search.")
    ET.SubElement(channel, f"{{{ATOM_NS}}}link", href=self_link, rel="self", type="application/rss+xml")
    newest = max((i["added"] for i in items), default=datetime.now(timezone.utc))
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(newest)
    for i in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = i["title"]
        ET.SubElement(item, "link").text = i["link"]
        ET.SubElement(item, "description").text = i["description"]
        ET.SubElement(item, "guid", isPermaLink="false").text = str(i["guid"])
        ET.SubElement(item, "pubDate").text = format_datetime(i["added"])
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)
