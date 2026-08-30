"""Deterministic mate transforms for staged CAD assemblies.

Positions in an assembly are computed here, from declared ports, never
authored by the model as raw at/rotate vectors. Reuses build123d's own
RigidJoint machinery (build123d>=0.11) rather than reimplementing rotation/
translation composition -- verified directly against the CAD runtime venv:
connecting a RigidJoint at a fixed part's port to a RigidJoint at a moving
part's port produces the expected global Location (bore-in-seat example:
a port at local (0,0,-4) connected to a seat at (20,0,5) places the moving
part's origin at (20,0,9) = seat - local_port, composing correctly).

A port is just a Location in a component's own local frame -- the point (and
orientation) other components attach to. Port-authoring convention: a port's
local +X axis points away from its own part's body, toward whatever it will
mate with. This matters only for `gear_mesh`'s center-distance offset
direction; `coaxial`/`face_to_face`/`press_fit` are direction-agnostic.
"""
from __future__ import annotations

from typing import NamedTuple

from build123d import Location, RigidJoint


class Port(NamedTuple):
    """A named attachment point on a component, in that component's own
    local frame (not yet placed in the assembly)."""
    location: Location


MATE_TYPES = ("coaxial", "face_to_face", "press_fit", "gear_mesh")

# press_fit and gear_mesh mates ARE the contact declaration -- a part
# assembled via one of these mate types is expected to touch its partner,
# and that expectation must exempt the pair from interference failures
# without the model separately maintaining an allowed_contact list.
EXEMPT_MATE_TYPES = frozenset({"press_fit", "gear_mesh"})


def _rigid_connect(fixed_part, fixed_port: Port, moving_part, moving_port: Port) -> Location:
    """Connect moving_part's port to fixed_part's port via a RigidJoint pair
    and return moving_part's resulting global Location. fixed_part is not
    moved; call sites are expected to have already placed it (or leave it at
    the assembly origin for the first part in an assembly)."""
    fixed_joint = RigidJoint("_mate_fixed", fixed_part, fixed_port.location)
    moving_joint = RigidJoint("_mate_moving", moving_part, moving_port.location)
    fixed_joint.connect_to(moving_joint)
    return moving_part.location


def coaxial(fixed_part, fixed_port: Port, moving_part, moving_port: Port) -> Location:
    """Align two named axes -- e.g. a pin seated in a bore."""
    return _rigid_connect(fixed_part, fixed_port, moving_part, moving_port)


def face_to_face(fixed_part, fixed_port: Port, moving_part, moving_port: Port) -> Location:
    """Coincident faces, opposing normals -- port orientation carries the
    "which way is out" convention; the join itself is a plain rigid connect."""
    return _rigid_connect(fixed_part, fixed_port, moving_part, moving_port)


def press_fit(fixed_part, fixed_port: Port, moving_part, moving_port: Port) -> Location:
    """Same geometry as `coaxial`; the distinct name only matters for
    `exempted_pairs` -- a press-fit pair is expected to interfere."""
    return _rigid_connect(fixed_part, fixed_port, moving_part, moving_port)


def gear_mesh(
    fixed_part, fixed_port: Port, moving_part, moving_port: Port,
    *, module: float, teeth_fixed: int, teeth_moving: int,
) -> Location:
    """Position two gears at their pitch-circle center distance
    (module * (teeth_fixed + teeth_moving) / 2) along the moving port's own
    local +X axis, then rigid-connect. Center distance is direction-agnostic
    (verified: the resulting center-to-center distance always equals the
    computed value); which side the gear lands on depends on the port
    authoring convention above."""
    center_distance = module * (teeth_fixed + teeth_moving) / 2
    offset_port = Port(location=moving_port.location * Location((center_distance, 0, 0)))
    return _rigid_connect(fixed_part, fixed_port, moving_part, offset_port)


_MATE_FUNCS = {
    "coaxial": coaxial,
    "face_to_face": face_to_face,
    "press_fit": press_fit,
}


def apply_mate(mate_type: str, fixed_part, fixed_port: Port, moving_part, moving_port: Port,
                **kwargs) -> Location:
    """Dispatch to the transform for `mate_type`. `gear_mesh` requires
    module/teeth_fixed/teeth_moving keyword arguments; the others ignore
    extra kwargs."""
    if mate_type == "gear_mesh":
        return gear_mesh(fixed_part, fixed_port, moving_part, moving_port, **kwargs)
    func = _MATE_FUNCS.get(mate_type)
    if func is None:
        raise ValueError(f"unknown mate type {mate_type!r}; expected one of {MATE_TYPES}")
    return func(fixed_part, fixed_port, moving_part, moving_port)


def exempted_pairs(mates: list[dict]) -> set[frozenset[str]]:
    """Derive the interference-exemption pair set directly from declared
    mates. Replaces a separately model-authored allowed_contact list: a
    declared press_fit or gear_mesh mate already says these two components
    are expected to touch, so there is exactly one place this fact lives.

    Each mate's `a`/`b` are "ComponentName.port_name" strings; only the
    component name (before the first ".") is used for exemption, matching
    the granularity interference findings are reported at.
    """
    pairs: set[frozenset[str]] = set()
    for mate in mates:
        if mate.get("type") not in EXEMPT_MATE_TYPES:
            continue
        a = str(mate.get("a", "")).split(".", 1)[0]
        b = str(mate.get("b", "")).split(".", 1)[0]
        if a and b and a != b:
            pairs.add(frozenset((a, b)))
    return pairs
