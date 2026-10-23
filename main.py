import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Apna Map India Navigation")

GOOGLE_ROUTES_URL = (
    "https://routes.googleapis.com/directions/v2:computeRoutes"
)

OVERPASS_SERVERS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


class RouteRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    destination_lat: float
    destination_lng: float


@app.get("/")
def home():
    return {
        "message": "Apna Map India Navigation Server is running"
    }


# =====================================
# 1. TRAFFIC-AWARE ROUTE API
# =====================================
@app.post("/route")
async def get_route(data: RouteRequest):
    api_key = os.getenv("GOOGLE_ROUTES_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_ROUTES_API_KEY Render mein set nahi hai"
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
        "languageCode": "en-US",
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
            "routes.legs.steps.startLocation"
        )
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                GOOGLE_ROUTES_URL,
                json=body,
                headers=headers
            )
            response.raise_for_status()
            result = response.json()

        routes = result.get("routes", [])

        if not routes:
            raise HTTPException(
                status_code=404,
                detail="Driving route nahi mila"
            )

        route = routes[0]
        instructions = []

        for leg in route.get("legs", []):
            for step in leg.get("steps", []):
                navigation = step.get(
                    "navigationInstruction", {}
                )

                instruction_text = navigation.get(
                    "instructions", "Continue straight"
                )

                start = (
                    step.get("startLocation", {})
                    .get("latLng", {})
                )

               step_duration = float(
    str(step.get("staticDuration", "0s")).rstrip("s")
)

                instructions.append({
                    "instruction": instruction_text,
                    "road_name": instruction_text,
                    "distance_meters": step.get(
                        "distanceMeters", 0
                    ),
                    "duration_seconds": step_duration,
                    # Android app ke existing format ke liye
                    # longitude pehle, latitude baad mein.
                    "location": [
                        start.get("longitude", 0),
                        start.get("latitude", 0)
                    ]
                })

        return {
            "distance_meters": route.get("distanceMeters", 0),
            "duration_seconds": float(
                str(route.get("duration", "0s")).rstrip("s")
            ),
            "encoded_polyline": (
                route["polyline"]["encodedPolyline"]
            ),
            "instructions": instructions
        }

    except HTTPException:
        raise

    except httpx.HTTPStatusError as error:
        # API key ko logs mein print nahi karna.
        print(
            "Google Routes API status error:",
            error.response.status_code,
            error.response.text[:500]
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Google Routes API error. "
                "API key, billing aur API restrictions check karo."
            )
        )

    except (httpx.HTTPError, KeyError, ValueError) as error:
        print("Route request error:", str(error))

        raise HTTPException(
            status_code=502,
            detail="Route service busy hai. Dobara try karo."
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

    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(
            status_code=502,
            detail="Destination search service busy hai"
        )


# =====================================
# 3. NEARBY SHOPS / PLACES API
# =====================================
@app.get("/nearby-shops")
async def nearby_shops(lat: float, lng: float):
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
                data = response.json()

            elements = data.get("elements", [])
            places = []

            for item in elements:
                tags = item.get("tags", {})
                center = item.get("center", {})

                place_lat = item.get(
                    "lat", center.get("lat")
                )
                place_lng = item.get(
                    "lon", center.get("lon")
                )

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
            print("Nearby places service error:", str(error))
            continue

    raise HTTPException(
        status_code=502,
        detail="Nearby places servers busy hain. Dobara try karo."
    )
