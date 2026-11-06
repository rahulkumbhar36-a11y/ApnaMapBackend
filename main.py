import os
import httpx

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="Apna Map India Smart Navigation")

GOOGLE_ROUTES_URL = (
    "https://routes.googleapis.com/directions/v2:computeRoutes"
)

GOOGLE_PLACES_URL = (
    "https://places.googleapis.com/v1/places:searchNearby"
)

GOOGLE_PLACES_TEXT_URL = (
    "https://places.googleapis.com/v1/places:searchText"
)


class RouteRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    destination_lat: float
    destination_lng: float


def duration_seconds(value):
    """Convert Google's duration string to seconds."""
    return float(str(value or "0s").removesuffix("s") or 0)


def get_google_places_key():
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_PLACES_API_KEY Render mein set nahi hai"
        )

    return api_key


# =====================================
# HOME
# =====================================

@app.get("/")
def home():
    return {
        "message": "Apna Map India Smart Navigation Server is running",
        "features": [
            "traffic_aware_navigation",
            "alternative_routes",
            "route_comparison",
            "navigation_instructions",
            "destination_search",
            "nearby_places"
        ],
        "nearby_categories": [
            "all",
            "shops",
            "hospital",
            "atm",
            "petrol_pump",
            "restaurant",
            "pharmacy"
        ]
    }


# =====================================
# ROUTE BUILDER
# =====================================

def build_route(route):

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
        "encoded_polyline": route.get(
            "polyline", {}
        ).get("encodedPolyline", ""),
        "traffic_intervals": route.get(
            "travelAdvisory", {}
        ).get("speedReadingIntervals", []),
        "route_labels": route.get(
            "routeLabels", []
        ),
        "instructions": instructions
    }


# =====================================
# 1. SMART MULTIPLE ROUTES
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
        "extraComputations": [
            "TRAFFIC_ON_POLYLINE"
        ],
        "computeAlternativeRoutes": True,
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
            "routes.routeLabels,"
            "routes.polyline.encodedPolyline,"
            "routes.travelAdvisory.speedReadingIntervals,"
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

            if response.status_code in (401, 403):
                raise HTTPException(
                    status_code=502,
                    detail="Google Routes API access ya billing check karo"
                )

            if response.status_code == 429:
                raise HTTPException(
                    status_code=502,
                    detail="Google Routes quota limit ho sakti hai"
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

        route_data = [
            build_route(route)
            for route in routes
        ]

        main_route = route_data[0]

        return {
            "distance_meters": main_route["distance_meters"],
            "duration_seconds": main_route["duration_seconds"],
            "encoded_polyline": main_route["encoded_polyline"],
            "traffic_intervals": main_route["traffic_intervals"],
            "instructions": main_route["instructions"],
            "route_labels": main_route["route_labels"],
            "alternative_routes": route_data[1:],
            "total_routes": len(route_data)
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

    except ValueError as error:
        print("Route response error:", str(error))

        raise HTTPException(
            status_code=502,
            detail="Google Routes response invalid hai"
        )


# =====================================
# 2. DESTINATION SEARCH - GOOGLE PLACES
# =====================================

@app.get("/search-place")
async def search_place(q: str):

    q = q.strip()

    if not q:
        raise HTTPException(
            status_code=400,
            detail="Destination ka naam likho"
        )

    api_key = get_google_places_key()

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.displayName,"
            "places.location,"
            "places.formattedAddress"
        )
    }

    body = {
        "textQuery": q,
        "maxResultCount": 5
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                GOOGLE_PLACES_TEXT_URL,
                headers=headers,
                json=body
            )

        if response.is_error:
            print(
                "Google Place Search error:",
                response.status_code,
                response.text[:1500]
            )

            if response.status_code in (401, 403):
                raise HTTPException(
                    status_code=502,
                    detail="Google Places API access ya billing check karo"
                )

            if response.status_code == 429:
                raise HTTPException(
                    status_code=502,
                    detail="Google Places quota limit ho sakti hai"
                )

            raise HTTPException(
                status_code=502,
                detail="Google Destination Search fail hui"
            )

        result = response.json()
        places = []

        for place in result.get("places", []):

            location = place.get("location", {})
            display_name = place.get("displayName", {})

            if (
                "latitude" not in location
                or "longitude" not in location
            ):
                continue

            places.append({
                "name": display_name.get("text", q),
                "latitude": location["latitude"],
                "longitude": location["longitude"]
            })

        return {
            "places": places
        }

    except HTTPException:
        raise

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Google Search timeout ho gaya"
        )

    except httpx.HTTPError as error:
        print(
            "Place search connection error:",
            str(error)
        )

        raise HTTPException(
            status_code=502,
            detail="Google Search se connection nahi hua"
        )

    except ValueError as error:
        print(
            "Place search response error:",
            str(error)
        )

        raise HTTPException(
            status_code=502,
            detail="Google Search response invalid hai"
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
    "pharmacy": ["pharmacy"]
}


@app.get("/nearby-shops")
async def nearby_shops(
    lat: float,
    lng: float,
    category: str = Query(default="all"),
    radius: int = Query(
        default=1500,
        ge=100,
        le=50000
    )
):

    if not (
        -90 <= lat <= 90
        and -180 <= lng <= 180
    ):
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
                "allowed_categories": list(
                    CATEGORY_TYPES.keys()
                )
            }
        )

    api_key = get_google_places_key()

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
            print(
                "Google Places API error:",
                response.status_code,
                response.text[:2000]
            )

            if response.status_code in (401, 403):
                raise HTTPException(
                    status_code=502,
                    detail="Google Places access ya billing check karo"
                )

            if response.status_code == 429:
                raise HTTPException(
                    status_code=502,
                    detail="Google Places quota limit ho sakti hai"
                )

            raise HTTPException(
                status_code=502,
                detail="Google Places search fail hui"
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

    except httpx.HTTPError as error:
        print(
            "Google Places connection error:",
            str(error)
        )

        raise HTTPException(
            status_code=502,
            detail="Google Places service se connection nahi hua"
        )

    except ValueError as error:
        print(
            "Google Places response error:",
            str(error)
        )

        raise HTTPException(
            status_code=502,
            detail="Google Places response invalid hai"
        )
