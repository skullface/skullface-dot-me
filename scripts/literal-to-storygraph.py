#!/usr/bin/env python3
"""
Export Literal.club library data to a Goodreads-format CSV for The StoryGraph.

StoryGraph only accepts Goodreads-style bulk imports:
  https://app.thestorygraph.com/import-goodreads

Usage:
  # Fetch from Literal API and write StoryGraph import CSV
  LITERAL_EMAIL=you@example.com LITERAL_PASSWORD='...' \\
    python3 literal-to-storygraph.py --fetch -o storygraph-import.csv

  # Or use an existing token (valid ~6 months)
  LITERAL_TOKEN='...' python3 literal-to-storygraph.py --fetch -o storygraph-import.csv

  # Convert a CSV you downloaded from Literal settings
  python3 literal-to-storygraph.py literal-export.csv -o storygraph-import.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

GRAPHQL_URL = "https://literal.club/graphql/"
EXPORT_URL = "https://literal.club/api/export/csv"

# Literal sits behind Cloudflare Browser Integrity Check. Python's default
# User-Agent (Python-urllib/...) triggers HTTP 403 / error 1010 unless we
# send browser-like headers.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def literal_request_headers(
    token: str | None = None,
    *,
    json_body: bool = False,
) -> dict[str, str]:
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://literal.club",
        "Referer": "https://literal.club/",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def format_http_error(exc: urllib.error.HTTPError) -> str:
    detail = exc.read().decode("utf-8", errors="replace").strip()
    if exc.code == 403 and "1010" in detail:
        return (
            f"Literal API HTTP {exc.code}: Cloudflare blocked this request (error 1010). "
            "Literal rejects non-browser HTTP clients. Update to the latest version of "
            "this script, or export from Literal in your browser and pass the CSV file "
            "instead of using --fetch."
        )
    if detail:
        return f"Literal API HTTP {exc.code}: {detail}"
    return f"Literal API HTTP {exc.code}"

GOODREADS_HEADERS = [
    "Book Id",
    "Title",
    "Author",
    "Author l-f",
    "Additional Authors",
    "ISBN",
    "ISBN13",
    "My Rating",
    "Average Rating",
    "Publisher",
    "Binding",
    "Number of Pages",
    "Year Published",
    "Original Publication Year",
    "Date Read",
    "Date Added",
    "Bookshelves",
    "Bookshelves with positions",
    "Exclusive Shelf",
    "My Review",
    "Spoiler",
    "Private Notes",
    "Read Count",
    "Owned Copies",
]

STATUS_TO_SHELF = {
    "FINISHED": "read",
    "IS_READING": "currently-reading",
    "WANTS_TO_READ": "to-read",
    "DROPPED": "did-not-finish",
    "NONE": "to-read",
    # Literal CSV / human-readable labels
    "finished": "read",
    "reading": "currently-reading",
    "is_reading": "currently-reading",
    "currently reading": "currently-reading",
    "want to read": "to-read",
    "wants to read": "to-read",
    "want": "to-read",
    "dropped": "did-not-finish",
    "dnf": "did-not-finish",
    "read": "read",
    "to-read": "to-read",
    "currently-reading": "currently-reading",
    "did-not-finish": "did-not-finish",
}


@dataclass
class BookRow:
    book_id: str
    title: str
    author: str
    isbn10: str = ""
    isbn13: str = ""
    status: str = "WANTS_TO_READ"
    date_added: str = ""
    date_read: str = ""
    rating: float = 0.0
    review: str = ""
    shelves: list[str] = field(default_factory=list)


class LiteralClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        request = urllib.request.Request(
            GRAPHQL_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=literal_request_headers(self.token, json_body=True),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(format_http_error(exc)) from exc

        if body.get("errors"):
            messages = "; ".join(err.get("message", str(err)) for err in body["errors"])
            raise RuntimeError(f"Literal API error: {messages}")
        return body.get("data") or {}

    @staticmethod
    def login(email: str, password: str) -> tuple[str, str]:
        query = """
        mutation Login($email: String!, $password: String!) {
          login(email: $email, password: $password) {
            token
            profile { id handle }
          }
        }
        """
        payload = {"query": query, "variables": {"email": email, "password": password}}
        request = urllib.request.Request(
            GRAPHQL_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=literal_request_headers(json_body=True),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(format_http_error(exc)) from exc
        if body.get("errors"):
            messages = "; ".join(err.get("message", str(err)) for err in body["errors"])
            raise RuntimeError(f"Login failed: {messages}")
        login_data = (body.get("data") or {}).get("login")
        if not login_data or not login_data.get("token"):
            raise RuntimeError("Login failed: no token returned")
        profile = login_data.get("profile") or {}
        return login_data["token"], profile.get("id", "")

    def download_literal_csv(self) -> str:
        request = urllib.request.Request(
            EXPORT_URL,
            headers=literal_request_headers(self.token),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(format_http_error(exc)) from exc


def parse_iso_date(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    if not value:
        return ""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(value, fmt).strftime("%Y/%m/%d")
        except ValueError:
            continue
    if "T" in value:
        return parse_iso_date(value.split("T", 1)[0].replace("-", "/"))
    return value.replace("-", "/")


def author_lf(name: str) -> str:
    parts = [p for p in name.strip().split() if p]
    if len(parts) <= 1:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def slugify_tag(name: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", name.strip().lower())
    return re.sub(r"[\s_]+", "-", cleaned).strip("-")


def normalize_status(raw: str) -> str:
    key = raw.strip().lower().replace("_", " ")
    key = re.sub(r"\s+", " ", key)
    return STATUS_TO_SHELF.get(raw.strip(), STATUS_TO_SHELF.get(key, "to-read"))


def read_count_for_shelf(exclusive_shelf: str) -> int:
    return 1 if exclusive_shelf == "read" else 0


def format_rating(value: float | int | str | None) -> str:
    if value in (None, "", 0, "0", 0.0):
        return "0"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if number <= 0:
        return "0"
    if number.is_integer():
        return str(int(number))
    return str(number)


def join_authors(authors: Iterable[str]) -> str:
    names = [a.strip() for a in authors if a and a.strip()]
    return ", ".join(names)


def fetch_books_from_api(client: LiteralClient, profile_id: str) -> list[BookRow]:
    reading_states_query = """
    query MyReadingStates {
      myReadingStates {
        id
        status
        bookId
        createdAt
        book {
          id
          title
          isbn10
          isbn13
          authors { name }
        }
      }
    }
    """
    export_query = """
    query MyBooksExport {
      myBooksExport {
        id
        title
        authors
        isbn10
        isbn13
        shelves
      }
    }
    """
    reviews_query = """
    query MyReviews($limit: Int!, $offset: Int!) {
      myReviews(limit: $limit, offset: $offset) {
        ... on BookReviewActivity {
          data {
            rating
            text
            book { id }
          }
          object
        }
      }
    }
    """
    read_dates_query = """
    query GetReadDates($bookId: String!, $profileId: String!) {
      getReadDates(bookId: $bookId, profileId: $profileId) {
        started
        finished
      }
    }
    """

    states = client.graphql(reading_states_query).get("myReadingStates") or []
    export_rows = client.graphql(export_query).get("myBooksExport") or []
    shelves_by_book: dict[str, list[str]] = {}
    for item in export_rows:
        book_id = item.get("id") or ""
        raw_shelves = item.get("shelves") or []
        if isinstance(raw_shelves, str):
            raw_shelves = [s.strip() for s in raw_shelves.split(",") if s.strip()]
        shelves_by_book[book_id] = [str(s).strip() for s in raw_shelves if str(s).strip()]

    reviews_by_book: dict[str, dict[str, Any]] = {}
    offset = 0
    page_size = 100
    while True:
        page = client.graphql(reviews_query, {"limit": page_size, "offset": offset}).get("myReviews") or []
        if not page:
            break
        for activity in page:
            data = activity.get("data") or {}
            book = data.get("book") or {}
            book_id = book.get("id") or activity.get("object") or ""
            if book_id:
                reviews_by_book[book_id] = data
        if len(page) < page_size:
            break
        offset += page_size

    books: dict[str, BookRow] = {}
    for state in states:
        book = state.get("book") or {}
        book_id = book.get("id") or state.get("bookId") or ""
        if not book_id:
            continue
        authors = [a.get("name", "") for a in book.get("authors") or [] if a.get("name")]
        if not authors and book_id in {row.get("id") for row in export_rows}:
            export_match = next((row for row in export_rows if row.get("id") == book_id), None)
            if export_match and export_match.get("authors"):
                if isinstance(export_match["authors"], list):
                    authors = [str(a) for a in export_match["authors"]]
                else:
                    authors = [export_match["authors"]]

        row = BookRow(
            book_id=book_id,
            title=(book.get("title") or "").strip(),
            author=join_authors(authors),
            isbn10=(book.get("isbn10") or "").strip(),
            isbn13=(book.get("isbn13") or "").strip(),
            status=(state.get("status") or "WANTS_TO_READ").strip(),
            date_added=parse_iso_date(state.get("createdAt")),
            shelves=shelves_by_book.get(book_id, []),
        )

        review = reviews_by_book.get(book_id)
        if review:
            row.rating = float(review.get("rating") or 0)
            row.review = (review.get("text") or "").strip()

        books[book_id] = row

    for book_id, row in books.items():
        if row.status not in {"FINISHED", "DROPPED"}:
            continue
        dates = client.graphql(
            read_dates_query,
            {"bookId": book_id, "profileId": profile_id},
        ).get("getReadDates") or []
        if not dates:
            continue
        latest = max(
            dates,
            key=lambda item: item.get("finished") or item.get("started") or "",
        )
        row.date_read = parse_iso_date(latest.get("finished") or latest.get("started"))

    return list(books.values())


def pick(row: dict[str, str], *names: str) -> str:
    lowered = {key.strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def parse_literal_csv(text: str) -> list[BookRow]:
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise RuntimeError("Literal CSV appears to have no header row")

    books: list[BookRow] = []
    for index, row in enumerate(reader, start=2):
        title = pick(row, "title", "book title", "name")
        if not title:
            continue

        author = pick(row, "author", "authors", "author(s)")
        status = pick(
            row,
            "status",
            "reading status",
            "readingstate",
            "reading state",
            "exclusive shelf",
            "shelf",
        )
        isbn13 = pick(row, "isbn13", "isbn 13", "isbn/uid", "isbn")
        isbn10 = pick(row, "isbn10", "isbn 10")
        if len(isbn13) == 10 and not isbn10:
            isbn10, isbn13 = isbn13, ""

        shelves_raw = pick(row, "shelves", "bookshelves", "tags", "custom shelves")
        shelves = []
        if shelves_raw:
            shelves = [s.strip() for s in re.split(r"[;,|]", shelves_raw) if s.strip()]

        rating_raw = pick(row, "rating", "my rating", "star rating", "score")
        review = pick(row, "review", "my review", "text")
        date_added = parse_iso_date(pick(row, "date added", "added", "createdat", "created at"))
        date_read = parse_iso_date(
            pick(row, "date read", "finished", "date finished", "last date read", "read date")
        )

        book_id = pick(row, "id", "book id", "bookid") or f"literal-{index}"
        books.append(
            BookRow(
                book_id=book_id,
                title=title,
                author=author,
                isbn10=isbn10,
                isbn13=isbn13,
                status=status or "WANTS_TO_READ",
                date_added=date_added,
                date_read=date_read,
                rating=float(rating_raw) if rating_raw else 0.0,
                review=review,
                shelves=shelves,
            )
        )
    return books


def to_goodreads_rows(books: Iterable[BookRow]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for book in books:
        exclusive_shelf = normalize_status(book.status)
        shelf_tags = [slugify_tag(name) for name in book.shelves if name]
        shelf_tags = [tag for tag in shelf_tags if tag and tag not in {
            "read",
            "to-read",
            "currently-reading",
            "did-not-finish",
        }]
        bookshelves = ", ".join(dict.fromkeys(shelf_tags))
        date_added = book.date_added or book.date_read or datetime.now().strftime("%Y/%m/%d")

        output.append(
            {
                "Book Id": book.book_id,
                "Title": book.title,
                "Author": book.author,
                "Author l-f": author_lf(book.author) if book.author else "",
                "Additional Authors": "",
                "ISBN": book.isbn10,
                "ISBN13": book.isbn13,
                "My Rating": format_rating(book.rating),
                "Average Rating": "",
                "Publisher": "",
                "Binding": "",
                "Number of Pages": "",
                "Year Published": "",
                "Original Publication Year": "",
                "Date Read": book.date_read if exclusive_shelf == "read" else "",
                "Date Added": date_added,
                "Bookshelves": bookshelves,
                "Bookshelves with positions": "",
                "Exclusive Shelf": exclusive_shelf,
                "My Review": book.review,
                "Spoiler": "",
                "Private Notes": "",
                "Read Count": str(read_count_for_shelf(exclusive_shelf)),
                "Owned Copies": "",
            }
        )
    return output


def write_goodreads_csv(path: str, rows: list[dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GOODREADS_HEADERS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(rows)


def resolve_token(args: argparse.Namespace) -> tuple[str, str]:
    token = args.token or os.environ.get("LITERAL_TOKEN", "").strip()
    profile_id = os.environ.get("LITERAL_PROFILE_ID", "").strip()

    if token:
        return token, profile_id

    email = os.environ.get("LITERAL_EMAIL", "").strip()
    password = os.environ.get("LITERAL_PASSWORD", "").strip()
    if not email or not password:
        raise RuntimeError(
            "Set LITERAL_TOKEN, or LITERAL_EMAIL and LITERAL_PASSWORD, to fetch from Literal."
        )
    token, profile_id = LiteralClient.login(email, password)
    return token, profile_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Literal.club library data to a StoryGraph-compatible Goodreads CSV."
    )
    parser.add_argument(
        "input_csv",
        nargs="?",
        help="Optional Literal CSV export from Settings. Omit when using --fetch.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="storygraph-import.csv",
        help="Output CSV path (default: storygraph-import.csv)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch library data from Literal via GraphQL instead of converting a CSV file.",
    )
    parser.add_argument(
        "--download-literal-csv",
        action="store_true",
        help="When used with --fetch, also save Literal's native CSV export alongside the StoryGraph file.",
    )
    parser.add_argument(
        "--token",
        help="Literal API token (overrides LITERAL_TOKEN).",
    )
    args = parser.parse_args()

    try:
        if args.fetch:
            token, profile_id = resolve_token(args)
            client = LiteralClient(token)
            if not profile_id:
                me = client.graphql("query { me { profile { id handle } } }")
                profile_id = ((me.get("me") or {}).get("profile") or {}).get("id", "")
            if not profile_id:
                raise RuntimeError("Could not determine your Literal profile id.")

            if args.download_literal_csv:
                literal_csv_path = os.path.splitext(args.output)[0] + ".literal.csv"
                literal_csv = client.download_literal_csv()
                with open(literal_csv_path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(literal_csv)
                print(f"Saved Literal export to {literal_csv_path}", file=sys.stderr)

            books = fetch_books_from_api(client, profile_id)
        else:
            if not args.input_csv:
                parser.error("Provide a Literal CSV file, or use --fetch.")
            with open(args.input_csv, encoding="utf-8-sig", newline="") as handle:
                books = parse_literal_csv(handle.read())

        if not books:
            raise RuntimeError("No books found to export.")

        rows = to_goodreads_rows(books)
        write_goodreads_csv(args.output, rows)
    except (RuntimeError, OSError, urllib.error.URLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {len(rows)} books to {args.output}", file=sys.stderr)
    print(
        "Import this file at https://app.thestorygraph.com/import-goodreads",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
