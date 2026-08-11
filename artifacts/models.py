"""Data models for the hotel reservation system."""

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional


ROOM_TYPES = {
    "single": 75.0,
    "double": 120.0,
    "suite": 250.0,
    "deluxe": 400.0,
}


@dataclass
class Room:
    """A hotel room with a number, type, and nightly rate."""
    number: str
    room_type: str
    rate: float
    status: str = "available"  # available, occupied, maintenance

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Room":
        return cls(
            number=data["number"],
            room_type=data["room_type"],
            rate=float(data["rate"]),
            status=data.get("status", "available"),
        )


@dataclass
class Customer:
    """A hotel customer / guest."""
    customer_id: str
    name: str
    email: str
    phone: str = ""
    bookings: list = field(default_factory=list)  # list of booking_ids

    def to_dict(self) -> dict:
        return {
            "customer_id": self.customer_id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "bookings": self.bookings,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Customer":
        return cls(
            customer_id=data["customer_id"],
            name=data["name"],
            email=data["email"],
            phone=data.get("phone", ""),
            bookings=data.get("bookings", []),
        )


@dataclass
class Booking:
    """A reservation linking a customer to a room for a date range."""
    booking_id: str
    customer_id: str
    room_number: str
    check_in: str       # ISO date string YYYY-MM-DD
    check_out: str      # ISO date string YYYY-MM-DD
    status: str = "confirmed"  # confirmed, checked_in, checked_out, cancelled
    total_cost: float = 0.0
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Booking":
        return cls(
            booking_id=data["booking_id"],
            customer_id=data["customer_id"],
            room_number=data["room_number"],
            check_in=data["check_in"],
            check_out=data["check_out"],
            status=data.get("status", "confirmed"),
            total_cost=float(data.get("total_cost", 0.0)),
            created_at=data.get("created_at", ""),
        )

    @property
    def nights(self) -> int:
        """Number of nights between check_in and check_out."""
        ci = datetime.strptime(self.check_in, "%Y-%m-%d").date()
        co = datetime.strptime(self.check_out, "%Y-%m-%d").date()
        return (co - ci).days

    def overlaps(self, other_check_in: str, other_check_out: str) -> bool:
        """Check if this booking's date range overlaps with another range.

        Bookings are [check_in, check_out) — checkout day is exclusive,
        so a guest checking out on the 15th doesn't conflict with someone
        checking in on the 15th.
        """
        ci = datetime.strptime(self.check_in, "%Y-%m-%d").date()
        co = datetime.strptime(self.check_out, "%Y-%m-%d").date()
        oci = datetime.strptime(other_check_in, "%Y-%m-%d").date()
        oco = datetime.strptime(other_check_out, "%Y-%m-%d").date()
        return ci < oco and oci < co