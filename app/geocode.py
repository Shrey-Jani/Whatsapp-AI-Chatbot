"""Optional address verification via LocationIQ (OSM-based, free tier, no card).

Soft check by design: if the key is absent, the address is empty, or the API errors/rate-limits,
verify() returns None and the caller accepts the address as typed - a geocoder outage or a
real-but-unmapped address must never block intake. Swapping to Google/Canada Post later is a
change to this one file.
"""
import httpx

from .config import settings

_URL = "https://us1.locationiq.com/v1/search"


def configured() -> bool:
    return bool(settings.geocode_api_key)


def verify(address: str) -> dict | None:
    """Return {'ok': bool, 'suggestion': str|None}, or None when the check can't run (→ accept)."""
    if not configured() or not (address or "").strip():
        return None
    try:
        r = httpx.get(_URL, timeout=6, params={
            "key": settings.geocode_api_key, "q": address, "format": "json",
            "limit": 1, "countrycodes": "ca", "addressdetails": 1, "normalizeaddress": 1})
        if r.status_code == 404:                 # LocationIQ signals "no match" with 404
            return {"ok": False, "suggestion": None}
        r.raise_for_status()
        data = r.json()
        if not data:
            return {"ok": False, "suggestion": None}
        top = data[0]
        # A real street resolves to a `road`. A fake street with a real city fuzzy-matches down to
        # the city/region centroid (no road) - treat that as NOT verified.
        addr = top.get("address") or {}
        if not addr.get("road"):
            return {"ok": False, "suggestion": None}
        return {"ok": True, "suggestion": top.get("display_name")}
    except Exception as e:                        # network / rate-limit / bad response → accept as typed
        print(f"[geocode] verify failed (accepting as typed): {e}")
        return None
