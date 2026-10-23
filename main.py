
import os
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Apna Map India Navigation")

GOOGLE_ROUTES_URL = (
    "https://routes.googleapis.com/directions/v2:computeRoutes"
)

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


class RouteRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    destination_lat: float
    destination_lng: float


def duration_seconds(value):
    """Convert Google's duration string, such as 120s, to seconds."""
    return float(str(value or "0s").removesuffix("s") or 0)


@app.get("/")
def home():
    return {
        "message": "Apna Map India Navigation Server is running"
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
        polyline = route.get("polyline", {}).get(
            "encodedPolyline"
        )

        if not polyline:
            raise HTTPException(
                status_code=502,
                detail="Google response mein route polyline nahi mila"
            )

        # Convert Google's steps into a simple app-friendly list.
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

                # Skip steps without instruction text.
                if not instruction_text:
                    continue

                instructions.append({
                    "instruction": instruction_text,
                    "maneuver": navigation.get(
                        "maneuver", "MANEUVER_UNSPECIFIED"
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
            "distance_meters": route.get("distanceMeters", 0),
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
            timeout=15,
            headers={"User-Agent": "ApnaMapIndia/1.0"}
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
# 3. NEARBY SHOPS / PLACES API
# =====================================
@app.get("/nearby-shops")
async def nearby_shops(lat: float, lng: float):
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(
            status_code=400,
            detail="Latitude ya longitude galat hai"
        )

    query = f"""
    [out:json][timeout:20];
    (
      nwr(around:1500,{lat},{lng})["shop"];
      nwr(around:1500,{lat},{lng})
        ["amenity"~"pharmacy|restaurant|hospital|fuel|atm"];
    );
    out center tags;
    """

    for server in OVERPASS_SERVERS:
        try:
            async with httpx.AsyncClient(
                timeout=35,
                headers={"User-Agent": "ApnaMapIndia/1.0"}
            ) as client:
                response = await client.post(
                    server,
                    data={"data": query}
                )
                response.raise_for_status()
                result = response.json()

            places = []

            for item in result.get("elements", []):
                tags = item.get("tags", {})
                center = item.get("center", {})

                place_lat = item.get("lat", center.get("lat"))
                place_lng = item.get("lon", center.get("lon"))

                if place_lat is None or place_lng is None:
                    continue

                places.append({
                    "name": tags.get(
                        "name",
                        tags.get(
                            "shop",
                            tags.get("amenity", "Nearby place")
                        )
                    ),
                    "latitude": place_lat,
                    "longitude": place_lng,
                    "category": tags.get(
                        "shop",
                        tags.get("amenity", "place")
                    )
                })

            return {"places": places}

        except (httpx.HTTPError, ValueError) as error:
            print("Nearby places server error:", str(error))
            continue

    raise HTTPException(
        status_code=502,
        detail="Nearby places servers busy hain. Dobara try karo."
)
