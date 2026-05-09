"""
contacts.py - Contact Book for AI BlackBox Flight Recorder

Per-operator contact storage with fuzzy search.
Storage: Contacts/contacts.json
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

# Path to contacts file
CONTACTS_DIR = Path(__file__).resolve().parent.parent / "Contacts"
CONTACTS_FILE = CONTACTS_DIR / "contacts.json"

# Seed contact added to every new operator's book
SEED_CONTACT = {
    "name": "AI BlackBox Flight Recorder",
    "phone": "+17164512527",
    "email": "brandon@aiblackboxfc.com",
    "relationship": "self",
    "notes": "This is your own phone number. The AI BlackBox system number. Use this as the caller identity.",
    "tags": ["system", "self"],
    "created_by": "system"
}


def load_contacts() -> Dict[str, Any]:
    """Read contacts.json. Creates file with {} if missing."""
    CONTACTS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONTACTS_FILE.exists():
        CONTACTS_FILE.write_text("{}")
        return {}
    try:
        return json.loads(CONTACTS_FILE.read_text())
    except (json.JSONDecodeError, IOError):
        return {}


def save_contacts(data: Dict[str, Any]) -> None:
    """Write full contacts dict to disk."""
    CONTACTS_DIR.mkdir(parents=True, exist_ok=True)
    CONTACTS_FILE.write_text(json.dumps(data, indent=2))


def _make_seed_contact() -> Dict[str, Any]:
    """Create a seed contact entry with generated ID and timestamps."""
    now = datetime.now(timezone.utc).isoformat()
    contact_id = str(uuid.uuid4())
    return {
        "id": contact_id,
        **SEED_CONTACT,
        "created_at": now,
        "updated_at": now
    }


def ensure_operator_book(data: Dict[str, Any], operator: str) -> bool:
    """
    Ensure operator has a phone book. Creates one with seed contact if missing.
    Returns True if a new book was created.
    """
    if operator not in data:
        seed = _make_seed_contact()
        data[operator] = {seed["id"]: seed}
        return True
    return False


def search_contacts(query: str, operator: str) -> List[Dict[str, Any]]:
    """
    Case-insensitive fuzzy match across all contact fields.
    Returns up to 10 matches, exact name matches ranked first.
    """
    data = load_contacts()
    if ensure_operator_book(data, operator):
        save_contacts(data)

    book = data.get(operator, {})
    query_lower = query.lower()
    results = []

    for contact in book.values():
        score = 0
        # Exact name match (highest priority)
        if contact.get("name", "").lower() == query_lower:
            score = 100
        # Partial name match
        elif query_lower in contact.get("name", "").lower():
            score = 80
        # Phone match
        elif query_lower in contact.get("phone", "").replace("+", "").replace("-", "").replace(" ", ""):
            score = 70
        # Email match
        elif query_lower in contact.get("email", "").lower():
            score = 60
        # Relationship match
        elif query_lower in contact.get("relationship", "").lower():
            score = 50
        # Tag match
        elif any(query_lower in tag.lower() for tag in contact.get("tags", [])):
            score = 40
        # Notes match
        elif query_lower in contact.get("notes", "").lower():
            score = 30

        if score > 0:
            results.append((score, contact))

    # Sort by score descending, return top 10
    results.sort(key=lambda x: x[0], reverse=True)
    return [contact for _, contact in results[:10]]


def upsert_contact(
    name: str,
    notes: str,
    tags: List[str],
    operator: str,
    created_by: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    relationship: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create or update a contact. Matches existing by name (case-insensitive).
    Returns the saved contact.
    """
    data = load_contacts()
    ensure_operator_book(data, operator)
    book = data[operator]
    now = datetime.now(timezone.utc).isoformat()

    # Check for existing contact with same name
    existing_id = None
    for cid, contact in book.items():
        if contact.get("name", "").lower() == name.lower():
            existing_id = cid
            break

    if existing_id:
        # Update existing
        contact = book[existing_id]
        contact["name"] = name
        contact["notes"] = notes
        contact["tags"] = tags
        if phone is not None:
            contact["phone"] = phone
        if email is not None:
            contact["email"] = email
        if relationship is not None:
            contact["relationship"] = relationship
        contact["updated_at"] = now
    else:
        # Create new
        contact_id = str(uuid.uuid4())
        contact = {
            "id": contact_id,
            "name": name,
            "phone": phone or "",
            "email": email or "",
            "relationship": relationship or "",
            "notes": notes,
            "tags": tags,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now
        }
        book[contact_id] = contact

    save_contacts(data)
    return contact
