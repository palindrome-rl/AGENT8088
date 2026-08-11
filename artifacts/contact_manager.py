"""A small contact manager that stores contacts in a local JSON file."""

import json
import os
import re
from datetime import datetime


def validate_phone(phone):
    """Return True if phone contains only digits, +, -, spaces, and parentheses."""
    if not phone or not isinstance(phone, str):
        return False
    return bool(re.match(r'^[\d+\-\s()]+$', phone.strip())) and bool(re.search(r'\d', phone))


class ContactManager:
    """Manage contacts persisted to a JSON file."""

    def __init__(self, json_file="contacts.json"):
        self.json_file = json_file
        self.contacts = {}
        self._load()

    # ------------------------------------------------------------------
    # persistence helpers
    # ------------------------------------------------------------------
    def _load(self):
        """Load contacts from the JSON file if it exists."""
        if os.path.exists(self.json_file):
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.contacts = {str(k): v for k, v in data.items()}
        else:
            self.contacts = {}

    def _save(self):
        """Write contacts to the JSON file."""
        with open(self.json_file, "w", encoding="utf-8") as f:
            json.dump(self.contacts, f, indent=2, sort_keys=True)

    def _next_id(self):
        if not self.contacts:
            return 1
        return max(int(k) for k in self.contacts.keys()) + 1

    def _phone_exists(self, phone, exclude_id=None):
        """Check if a phone number already exists (optionally excluding a contact id)."""
        phone_clean = phone.strip()
        for cid, contact in self.contacts.items():
            if exclude_id is not None and str(cid) == str(exclude_id):
                continue
            if contact["phone"].strip() == phone_clean:
                return True
        return False

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------
    def add_contact(self, name, phone, email=""):
        """Add a new contact and persist it. Returns the contact dict."""
        if not name or not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if not validate_phone(phone):
            raise ValueError("phone must be a non-empty string containing at least one digit "
                             "and only digits, +, -, spaces, and parentheses")
        if self._phone_exists(phone):
            raise ValueError(f"A contact with phone '{phone}' already exists")

        contact_id = self._next_id()
        contact = {
            "id": contact_id,
            "name": name.strip(),
            "phone": phone.strip(),
            "email": email.strip() if isinstance(email, str) else "",
            "created": datetime.now().isoformat(),
        }
        self.contacts[str(contact_id)] = contact
        self._save()
        return contact

    def get_contact(self, contact_id):
        """Return a single contact by id, or None if not found."""
        return self.contacts.get(str(contact_id))

    def list_contacts(self):
        """Return all contacts as a list of dicts."""
        return list(self.contacts.values())

    def edit_contact(self, contact_id, **kwargs):
        """Edit fields on an existing contact. Returns the updated contact."""
        contact_id = str(contact_id)
        if contact_id not in self.contacts:
            raise KeyError(f"Contact {contact_id} not found")

        editable = {"name", "phone", "email"}
        for key, value in kwargs.items():
            if key not in editable:
                raise ValueError(f"'{key}' is not an editable field")

        # Validate name if provided
        if "name" in kwargs:
            name = kwargs["name"]
            if not name or not isinstance(name, str) or not name.strip():
                raise ValueError("name must be a non-empty string")

        # Validate phone if provided
        if "phone" in kwargs:
            phone = kwargs["phone"]
            if not validate_phone(phone):
                raise ValueError("phone must be a non-empty string containing at least one digit "
                                 "and only digits, +, -, spaces, and parentheses")
            if self._phone_exists(phone, exclude_id=contact_id):
                raise ValueError(f"A contact with phone '{phone}' already exists")

        # Apply changes
        for key, value in kwargs.items():
            if key in ("name", "phone", "email"):
                self.contacts[contact_id][key] = value.strip() if isinstance(value, str) else value

        self._save()
        return self.contacts[contact_id]

    def delete_contact(self, contact_id):
        """Delete a contact by id. Returns True on success."""
        contact_id = str(contact_id)
        if contact_id not in self.contacts:
            raise KeyError(f"Contact {contact_id} not found")
        del self.contacts[contact_id]
        self._save()
        return True

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def search_contacts(self, keyword=None):
        """Search contacts by name or phone number (case-insensitive substring match)."""
        if keyword is None:
            return self.list_contacts()

        kw = keyword.lower().strip()
        results = []
        for contact in self.contacts.values():
            if kw in contact["name"].lower() or kw in contact["phone"].lower():
                results.append(contact)
        return results


if __name__ == "__main__":
    import tempfile

    tmp = tempfile.mktemp(suffix=".json")
    mgr = ContactManager(tmp)
    mgr.add_contact("Alice Smith", "555-1234", "alice@example.com")
    mgr.add_contact("Bob Jones", "555-5678")
    print(json.dumps(mgr.list_contacts(), indent=2))
    os.remove(tmp)