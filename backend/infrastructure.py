"""Regional egress aggregates for the analyst globe."""

import math
import re

EGRESS_TYPES = {"vpn", "proxy", "datacenter"}


def globe_points(snapshots: list[dict]) -> dict:
    markers = {}
    unmapped = 0
    for snapshot in snapshots:
        for node in snapshot["nodes"]:
            lat, lon = node.get("lat"), node.get("lon")
            comments = node["comments"]
            if not _valid_coord(lat, 90) or not _valid_coord(lon, 180):
                unmapped += comments
                continue
            # Regional markers avoid exposing precise locations or individual network addresses.
            lat, lon = round(lat, 1), round(lon, 1)
            if lon == 180:
                lon = -180
            key = (lat, lon)
            marker = markers.setdefault(key, {
                "lat": lat, "lon": lon, "comments": 0, "flagged": 0, "last": None,
                "locations": set(), "asns": set(), "platforms": set(), "threads": set(),
                "accounts": set(), "shared_egress": False,
            })
            marker["comments"] += comments
            marker["flagged"] += node["flagged"]
            marker["accounts"].update(node["users"])
            marker["threads"].add(snapshot["thread_id"])
            if snapshot["platform"]:
                marker["platforms"].add(snapshot["platform"])
            if node.get("last") is not None:
                marker["last"] = max(marker["last"] or 0, node["last"])
            location = ", ".join(value for value in (node.get("city"), node.get("cc")) if isinstance(value, str) and value)
            if location:
                marker["locations"].add(location)
            asn = node.get("asn")
            if isinstance(asn, str) and re.fullmatch(r"AS\d{1,10}", asn, flags=re.ASCII):
                marker["asns"].add(asn)
            # A network marker is only a lead for analysts. Shared hosting by itself is insufficient.
            if (node.get("type") in EGRESS_TYPES and len(node["users"]) >= 3
                    and node["flagged"] >= max(3, comments / 2)):
                marker["shared_egress"] = True

    points = []
    for marker in markers.values():
        points.append({
            **{key: marker[key] for key in ("lat", "lon", "comments", "flagged", "last", "shared_egress")},
            "locations": sorted(marker["locations"]),
            "asns": sorted(marker["asns"]),
            "platforms": sorted(marker["platforms"]),
            "thread_count": len(marker["threads"]),
            "account_count": len(marker["accounts"]),
        })
    points.sort(key=lambda point: (-point["comments"], point["lat"], point["lon"]))
    return {"points": points, "mapped_comments": sum(point["comments"] for point in points),
            "analyzed_comments": sum(snapshot["comments"] for snapshot in snapshots),
            "unmapped_egress_comments": unmapped}


def _valid_coord(value, limit: int) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and -limit <= value <= limit
