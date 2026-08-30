"""Factory layer for standardized mechanical elements: wraps bd_warehouse
objects (raw geometry, no attachment-point concept) with the named ports
`cad_mates.py` needs. Building a part this way costs zero model turns --
`kind: "custom"` is the only escape hatch that goes through the model at all.

Port coordinates below were measured directly against real bd_warehouse
0.3.0 objects in this repo's pinned CAD runtime (build123d==0.11.1), not
assumed from documentation:

- `SocketHeadCapScrew(size=..., length=L)` is naturally constructed with its
  bearing face (underside of the head) at local (0,0,0) and its shank
  extending to (0,0,-L) -- verified: length=20 gives a bounding box of
  z in [-20, 6], i.e. the shank occupies exactly [-20, 0] and the head sits
  above 0.
- `SpurGear(module=..., tooth_count=..., thickness=T)` is naturally
  constructed centered on its own axis, spanning z in [-T/2, T/2] -- its
  bore axis passes through local (0,0,0) already.

Neither object should be `.locate()`d before reading these ports -- doing so
changes which placement the natural, as-constructed coordinates above refer
to.
"""
from __future__ import annotations

from typing import Any

WAREHOUSE_KINDS = ("warehouse.fastener", "warehouse.gear")

# Ports are returned as plain {"at": (x,y,z), "axis": (x,y,z)} tuples, not
# Location objects -- this is the only form that needs to survive a
# round-trip through JSON (create() stores it in the manifest; finalize()
# rebuilds real cad_mates.Port objects from these exact numbers via
# cad_mates.port_from_axis). Building a Port here would just be discarded.


def _fastener(params: dict[str, Any]):
    from bd_warehouse.fastener import SocketHeadCapScrew

    fastener_type = str(params.get("type") or "SocketHeadCapScrew")
    if fastener_type != "SocketHeadCapScrew":
        raise ValueError(
            f"unsupported fastener type {fastener_type!r}; only 'SocketHeadCapScrew' "
            "is wired up so far"
        )
    size = params.get("size")
    length = params.get("length")
    if not size or length is None:
        raise ValueError("warehouse.fastener requires 'size' and 'length' params")
    part = SocketHeadCapScrew(size=str(size), length=float(length))
    ports = {"bearing_face": {"at": (0.0, 0.0, 0.0), "axis": (0.0, 0.0, 1.0)}}
    return part, ports


def _gear(params: dict[str, Any]):
    from bd_warehouse.gear import SpurGear

    required = ("module", "tooth_count", "pressure_angle", "thickness")
    missing = [key for key in required if params.get(key) is None]
    if missing:
        raise ValueError(f"warehouse.gear is missing required params: {', '.join(missing)}")
    part = SpurGear(
        module=float(params["module"]),
        tooth_count=int(params["tooth_count"]),
        pressure_angle=float(params["pressure_angle"]),
        thickness=float(params["thickness"]),
    )
    ports = {"bore": {"at": (0.0, 0.0, 0.0), "axis": (0.0, 0.0, 1.0)}}
    return part, ports


_RESOLVERS = {
    "warehouse.fastener": _fastener,
    "warehouse.gear": _gear,
}


def resolve(kind: str, params: dict[str, Any]) -> tuple[Any, dict[str, dict[str, tuple]]]:
    """Build a warehouse-kind part and return (geometry, {port_name: {at, axis}}).

    Raises ValueError for an unknown kind or invalid/missing params -- the
    caller (cad_project.create, via a worker round trip) reports this back
    plainly rather than silently falling through to a custom/model-authored
    build."""
    resolver = _RESOLVERS.get(kind)
    if resolver is None:
        raise ValueError(f"unknown warehouse kind {kind!r}; expected one of {WAREHOUSE_KINDS}")
    return resolver(params)
