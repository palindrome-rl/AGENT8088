"""A simple command-line library management system with JSON persistence."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Book:
    """A single book in the library."""

    title: str
    author: str
    isbn: str
    checked_out: bool = False
    borrower: Optional[str] = None


class Library:
    """Manages a collection of books and persists them to a JSON file."""

    def __init__(self, data_file: str = "library.json") -> None:
        self.data_file = data_file
        self.books: dict[str, Book] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _load(self) -> None:
        """Load books from the JSON file, if it exists."""
        if not os.path.exists(self.data_file):
            return
        try:
            with open(self.data_file, "r", encoding="utf-8") as fh:
                records = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable file: start fresh rather than crash.
            self.books = {}
            return
        self.books = {
            rec["isbn"]: Book(
                title=rec["title"],
                author=rec["author"],
                isbn=rec["isbn"],
                checked_out=rec.get("checked_out", False),
                borrower=rec.get("borrower"),
            )
            for rec in records
        }

    def _save(self) -> None:
        """Write the current collection to the JSON file."""
        with open(self.data_file, "w", encoding="utf-8") as fh:
            json.dump([asdict(b) for b in self.books.values()], fh, indent=2)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def add_book(self, title: str, author: str, isbn: str) -> Book:
        """Add a new book. Raises ValueError if the ISBN already exists."""
        if isbn in self.books:
            raise ValueError(f"A book with ISBN {isbn!r} already exists.")
        book = Book(title=title, author=author, isbn=isbn)
        self.books[isbn] = book
        self._save()
        return book

    def check_out(self, isbn: str, borrower: str) -> None:
        """Check a book out to a borrower.

        Raises ValueError if the book is not found or already checked out.
        """
        book = self.books.get(isbn)
        if book is None:
            raise ValueError(f"No book with ISBN {isbn!r}.")
        if book.checked_out:
            raise ValueError(
                f"{book.title!r} is already checked out by {book.borrower!r}."
            )
        book.checked_out = True
        book.borrower = borrower
        self._save()

    def check_in(self, isbn: str) -> None:
        """Return a checked-out book.

        Raises ValueError if the book is not found or not checked out.
        """
        book = self.books.get(isbn)
        if book is None:
            raise ValueError(f"No book with ISBN {isbn!r}.")
        if not book.checked_out:
            raise ValueError(f"{book.title!r} is not checked out.")
        book.checked_out = False
        book.borrower = None
        self._save()

    # ------------------------------------------------------------------
    # Listing / searching
    # ------------------------------------------------------------------
    def list_books(self) -> None:
        """Print a formatted table of all books and their status."""
        self._print_table(self.books.values())

    def search(self, query: str) -> None:
        """Print books whose title or author matches the query (case-insensitive)."""
        q = query.lower()
        matches = [
            b
            for b in self.books.values()
            if q in b.title.lower() or q in b.author.lower()
        ]
        self._print_table(matches)

    @staticmethod
    def _print_table(books) -> None:
        """Render a list of books as a simple aligned table."""
        if not books:
            print("No books found.")
            return
        header = f"{'Title':<30} {'Author':<22} {'ISBN':<15} {'Status'}"
        print(header)
        print("-" * len(header))
        for b in books:
            status = (
                f"Checked out by {b.borrower}"
                if b.checked_out
                else "Available"
            )
            print(f"{b.title:<30} {b.author:<22} {b.isbn:<15} {status}")


if __name__ == "__main__":
    # Small demo: add a couple of books, check one out, list, and search.
    lib = Library("library.json")

    print("Adding books...")
    lib.add_book("The Pragmatic Programmer", "Andrew Hunt", "978-0201616224")
    lib.add_book("The Phoenix Project", "Gene Kim", "978-0988262591")

    print("\nChecking out 'The Pragmatic Programmer' to Alice...")
    lib.check_out("978-0201616224", "Alice")

    print("\nAll books:")
    lib.list_books()

    print("\nSearch for 'pragmatic':")
    lib.search("pragmatic")
