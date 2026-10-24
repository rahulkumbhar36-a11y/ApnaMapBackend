import os
import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Apna Map India Navigation")

GOOGLE_ROUTES_URL = (
    "https://routes.googleapis.com/directions/v2:computeRoutes"
)

GOOGLE_PLACES_URL = (
    "https://places.googleapis.com/v1/places:searchNearby"
)


class RouteRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    destination_lat: float
    destination_lng: float


def duration_seconds(value):
    """Convert Google's duration string, such as 120s, to seconds."""
    return float(str(value or "0s").removesuffix("s") or 0)


# =====================================
# HOME
# =====================================
@app.get("/")
def home():
    return {
        "message": "Apna Map India Navigation Server is running",
        "nearby_categories": [
            "all",
            "shops",
            "hospital",
            "atm",
            "petrol_pump",
            "restaurant",
            "pharmacy",
        ],
    }


# =====================================
# 1. TRAFFIC-AWARE ROUTE + DIRECTIONS
# =====================================
@app.post("/route")
async def get_route(data: RouteRequest):
    api_key = os.getenv("GOOGLE_ROUTES_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_ROUTES_API_KEY Render mein set nahi hai"
        )

    if not (
        -90 <= data.origin_lat <= 90
        and -180 <= data.origin_lng <= 180
        and -90 <= data.destination_lat <= 90
        and -180 <= data.destination_lng <= 180
    ):
        raise HTTPException(
            status_code=400,
            detail="Latitude ya longitude galat hai"
        )

    body = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": data.origin_lat,
                    "longitude": data.origin_lng
                }
            }
        },
        "destination": {
            "location": {
                "latLng": {
                    "latitude": data.destination_lat,
                    "longitude": data.destination_lng
                }
            }
        },
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "computeAlternativeRoutes": False,
        "polylineEncoding": "ENCODED_POLYLINE",
        "languageCode": "hi-IN",
        "units": "METRIC"
    }

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "routes.distanceMeters,"
            "routes.duration,"
            "routes.polyline.encodedPolyline,"
            "routes.legs.steps.distanceMeters,"
            "routes.legs.steps.staticDuration,"
            "routes.legs.steps.navigationInstruction.instructions,"
            "routes.legs.steps.navigationInstruction.maneuver,"
            "routes.legs.steps.startLocation.latLng"
        )
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                GOOGLE_ROUTES_URL,
                json=body,
                headers=headers
            )

        if response.is_error:
            print(
                "Google Routes API error:",
                response.status_code,
                response.text[:1500]
            )

            if response.status_code == 400:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Google Routes request invalid hai. "
                        "Render Logs mein Google error check karo."
                    )
                )

            if response.status_code in (401, 403):
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Google API key, Routes API access, "
                        "billing ya restrictions check karo."
                    )
                )

            raise HTTPException(
                status_code=502,
                detail="Google Routes API request fail hui"
            )

        result = response.json()
        routes = result.get("routes", [])

        if not routes:
            raise HTTPException(
                status_code=404,
                detail="Driving route nahi mila"
            )

        route = routes[0]
        polyline = route.get(
            "polyline", {}
        ).get("encodedPolyline")

        if not polyline:
            raise HTTPException(
                status_code=502,
                detail="Google response mein route polyline nahi mila"
            )

        instructions = []

        for leg in route.get("legs", []):
            for step in leg.get("steps", []):
                navigation = step.get(
                    "navigationInstruction", {}
                )

                start_location = step.get(
                    "startLocation", {}
                ).get("latLng", {})

                instruction_text = navigation.get(
                    "instructions", ""
                )

                if not instruction_text:
                    continue

                instructions.append({
                    "instruction": instruction_text,
                    "maneuver": navigation.get(
                        "maneuver",
                        "MANEUVER_UNSPECIFIED"
                    ),
                    "distance_meters": step.get(
                        "distanceMeters", 0
                    ),
                    "duration_seconds": duration_seconds(
                        step.get("staticDuration", "0s")
                    ),
                    # [longitude, latitude]
                    "location": [
                        start_location.get("longitude", 0),
                        start_location.get("latitude", 0)
                    ]
                })

        return {
            "distance_meters": route.get(
                "distanceMeters", 0
            ),
            "duration_seconds": duration_seconds(
                route.get("duration", "0s")
            ),
            "encoded_polyline": polyline,
            "instructions": instructions
        }

    except HTTPException:
        raise

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Google Routes API timeout ho gaya"
        )

    except httpx.HTTPError as error:
        print("Route connection error:", str(error))
        raise HTTPException(
            status_code=502,
            detail="Google Routes service se connection nahi hua"
        )


# =====================================
# 2. DESTINATION SEARCH API
# =====================================
@app.get("/search-place")
async def search_place(q: str):
    q = q.strip()

    if not q:
        raise HTTPException(
            status_code=400,
            detail="Destination ka naam likho"
        )

    try:
        async with httpx.AsyncClient(
            timeout=40,
            headers={
                "User-Agent": "ApnaMapIndia/1.0"
            }
        ) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": q,
                    "format": "jsonv2",
                    "limit": 1
                }
            )
            response.raise_for_status()
            results = response.json()

        if not results:
            return {"places": []}

        place = results[0]

        return {
            "places": [{
                "name": place.get("display_name", q),
                "latitude": float(place["lat"]),
                "longitude": float(place["lon"])
            }]
        }

    except (httpx.HTTPError, ValueError, KeyError) as error:
        print("Place search error:", str(error))
        raise HTTPException(
            status_code=502,
            detail="Destination search service busy hai"
        )


# =====================================
# 3. NEARBY SEARCH USING GOOGLE PLACES
# =====================================
CATEGORY_TYPES = {
    "all": [
        "hospital",
        "atm",
        "gas_station",
        "restaurant",
        "pharmacy",
        "store"
    ],
    "shops": ["store"],
    "hospital": ["hospital"],
    "atm": ["atm"],
    "petrol_pump": ["gas_station"],
    "restaurant": ["restaurant"],
    "pharmacy": ["pharmacy"],
}


@app.get("/nearby-shops")
async def nearby_shops(
    lat: float,
    lng: float,
    category: str = Query(default="all"),
    radius: int = Query(default=1500, ge=100, le=50000)
):
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(
            status_code=400,
            detail="Latitude ya longitude galat hai"
        )

    category = category.strip().lower()

    if category not in CATEGORY_TYPES:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Unknown category",
                "allowed_categories": list(CATEGORY_TYPES.keys())
            }
        )

    api_key = os.getenv("GOOGLE_PLACES_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_PLACES_API_KEY Render mein set nahi hai"
        )

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,"
            "places.displayName,"
            "places.location,"
            "places.primaryType,"
            "places.formattedAddress"
        )
    }

    body = {
        "includedTypes": CATEGORY_TYPES[category],
        "maxResultCount": 20,
        "rankPreference": "DISTANCE",
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": lat,
                    "longitude": lng
                },
                "radius": float(radius)
            }
        }
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                GOOGLE_PLACES_URL,
                headers=headers,
                json=body
            )

        if response.is_error:
            # Detailed error appears in Render Logs.
            print(
                "Google Places API error:",
                response.status_code,
                response.text[:2000]
            )

            if response.status_code in (401, 403):
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Google Places access denied. "
                        "API enablement, key restrictions aur "
                        "billing check karo."
                    )
                )

            if response.status_code == 429:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Google Places quota ya rate limit "
                        "exceeded ho sakti hai."
                    )
                )

            raise HTTPException(
                status_code=502,
                detail=(
                    "Google Places search fail hui. "
                    "Render Logs check karo."
                )
            )

        result = response.json()
        places = []

        for item in result.get("places", []):
            location = item.get("location", {})
            display_name = item.get("displayName", {})

            if (
                "latitude" not in location
                or "longitude" not in location
            ):
                continue

            places.append({
                "name": display_name.get(
                    "text", "Nearby place"
                ),
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "category": item.get(
                    "primaryType", category
                ),
                "address": item.get(
                    "formattedAddress", ""
                )
            })

        return {
            "category": category,
            "radius_meters": radius,
            "count": len(places),
            "places": places
        }

    except HTTPException:
        raise

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Google Places API timeout ho gaya"
        )

    except (httpx.HTTPError, ValueError) as error:
        print("Google Places connection error:", str(error))
        raise HTTPException(
            status_code=502,
            detail="Google Places service se connection nahi hua"
        )
