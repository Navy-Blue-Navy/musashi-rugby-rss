import os
import sys
import time
import re
import requests
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin, urlparse


LIST_URL = "https://musashi-rugby.net/news/"
OUTPUT_FILE = "musashi_rugby.xml"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/156.0.0.0 Safari/537.36"
    )
}


def fetch_page(url):
    last_error = None

    for attempt in range(3):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )

            if response.status_code == 200:
                response.encoding = response.apparent_encoding
                return response

            last_error = RuntimeError(
                f"HTTP {response.status_code}: {url}"
            )

            if response.status_code == 403:
                break

            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue

            break

        except requests.RequestException as e:
            last_error = e

            if attempt < 2:
                time.sleep(5 * (attempt + 1))

    raise RuntimeError(f"ページ取得失敗: {last_error}")


def load_existing_items():
    items = {}

    if not os.path.exists(OUTPUT_FILE):
        return items

    tree = ET.parse(OUTPUT_FILE)
    root = tree.getroot()

    for item in root.findall("./channel/item"):
        link = item.findtext("link")

        if link:
            items[link] = {
                "title": item.findtext("title") or "",
                "link": link,
                "description": item.findtext("description") or "",
                "pubDate": item.findtext("pubDate") or "",
            }

    return items


def scrape_articles():
    response = fetch_page(LIST_URL)
    soup = BeautifulSoup(response.text, "html.parser")

    articles = {}

    # 一覧にある記事リンクを順番に調べる
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        url = urljoin(LIST_URL, href)

        parsed = urlparse(url)

        # 武蔵大学ラグビー部サイト以外は除外
        if parsed.netloc != "musashi-rugby.net":
            continue

        # 新着情報の記事リンクのみ
        if not (
            parsed.path.startswith("/info/")
            or parsed.path.startswith("/result/")
        ):
            continue

        title = a.get_text(" ", strip=True)

        if not title:
            continue

        # 同じ記事が複数回現れた場合は除外
        if url in articles:
            continue

        # リンク周辺から日付を探す
        date_text = ""

        node = a

        for _ in range(6):
            if node is None:
                break

            text = node.get_text(" ", strip=True)

            matches = re.findall(
                r"20\d{2}/\d{1,2}/\d{1,2}",
                text,
            )

            if matches:
                date_text = matches[0]
                break

            node = node.parent

        # 親要素で取れない場合は直前の文字列から探す
        if not date_text:
            previous = a.find_all_previous(string=True, limit=20)

            for text_node in previous:
                match = re.search(
                    r"20\d{2}/\d{1,2}/\d{1,2}",
                    str(text_node),
                )

                if match:
                    date_text = match.group(0)
                    break

        if not date_text:
            continue

        try:
            pub_dt = datetime.strptime(
                date_text,
                "%Y/%m/%d",
            ).replace(tzinfo=JST)
        except ValueError:
            continue

        articles[url] = {
            "title": title,
            "link": url,
            "description": title,
            "pubDate": format_datetime(pub_dt),
        }

    if not articles:
        raise RuntimeError(
            "記事を1件も取得できませんでした。RSSは更新しません。"
        )

    return articles


def build_rss(items):
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = (
        "武蔵大学ラグビー部 新着情報"
    )
    ET.SubElement(channel, "link").text = LIST_URL
    ET.SubElement(channel, "description").text = (
        "武蔵大学ラグビー部オフィシャルサイトの新着情報"
    )
    ET.SubElement(channel, "language").text = "ja"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(JST)
    )

    def sort_key(item):
        try:
            return parsedate_to_datetime(item.get("pubDate", ""))
        except Exception:
            return datetime(1970, 1, 1, tzinfo=timezone.utc)

    sorted_items = sorted(
        items.values(),
        key=sort_key,
        reverse=True,
    )

    for data in sorted_items:
        item = ET.SubElement(channel, "item")

        ET.SubElement(item, "title").text = data["title"]
        ET.SubElement(item, "link").text = data["link"]
        ET.SubElement(item, "guid").text = data["link"]
        ET.SubElement(item, "description").text = data["description"]

        if data.get("pubDate"):
            ET.SubElement(item, "pubDate").text = data["pubDate"]

    tree = ET.ElementTree(rss)

    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )


def main():
    print("武蔵大学ラグビー部の新着情報を取得します")

    new_items = scrape_articles()
    print(f"Webから取得: {len(new_items)}件")

    existing_items = load_existing_items()
    print(f"既存RSS: {len(existing_items)}件")

    merged = existing_items.copy()
    merged.update(new_items)

    print(f"統合後: {len(merged)}件")

    build_rss(merged)

    print(f"RSSを保存しました: {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)