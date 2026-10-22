from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI(title="Apna Map India Navigation")

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

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
# 1. ROUTE / NAVIGATION API
# =====================================
@app.post("/route")
async def get_route(data: RouteRequest):
    url = (
        f"{OSRM_URL}/"
        f"{data.origin_lng},{data.origin_lat};"
        f"{data.destination_lng},{data.destination_lat}"
    )

    params = {
        "overview": "full",
        "geometries": "polyline",
        "steps": "true",
        "alternatives": "false"
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()

        result = response.json()

        if result.get("code") != "Ok" or not result.get("routes"):
            raise HTTPException(
                status_code=404,
                detail="Driving route nahi mila"
            )

        route = result["routes"][0]
        instructions = []

        for leg in route.get("legs", []):
            for step in leg.get("steps", []):
                maneuver = step.get("maneuver", {})
                instruction_type = maneuver.get("type", "")
                modifier = maneuver.get("modifier", "")
                road_name = step.get("name") or "aage ka raasta"

                if instruction_type == "depart":
                    instruction_text = f"{road_name} par aage badhein"
                elif instruction_type == "arrive":
                    instruction_text = "Aap apni manzil par pahunch gaye"
                elif instruction_type == "turn":
                    instruction_text = f"{modifier or 'aage'} mudein"
                elif instruction_type == "roundabout":
                    instruction_text = "Roundabout mein aage badhein"
                elif instruction_type == "continue":
                    instruction_text = f"{road_name} par seedhe chalein"
                else:
                    instruction_text = f"{road_name} par aage badhein"

                instructions.append({
                    "instruction": instruction_text,
                    "road_name": road_name,
                    "distance_meters": step.get("distance", 0),
                    "duration_seconds": step.get("duration", 0),
                    "location": maneuver.get("location", [])
                })

        return {
            "distance_meters": route["distance"],
            "duration_seconds": route["duration"],
            "encoded_polyline": route["geometry"],
            "instructions": instructions
        }

    except HTTPException:
        raise
    except httpx.HTTPError:
        raise HTTPException(
            status_code=502,
            detail="Internet ya route service check karo"
        )


# =====================================
# 2. NEARBY SHOPS / PLACES API
# =====================================
# =====================================
# DESTINATION SEARCH API
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
@app.get("/nearby-shops")
async def nearby_shops(lat: float, lng: float):
    # Search within 1500 metres.
    query = f"""
    [out:json][timeout:20];
    (
      nwr(around:1500,{lat},{lng})["shop"];
      nwr(around:1500,{lat},{lng})
        ["amenity"~"pharmacy|restaurant|hospital|fuel|atm"];
    );
    out center tags;
    """

    last_error = None

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
            last_error = error
            continue

    raise HTTPException(
        status_code=502,
        detail="Nearby places servers busy hain. Dobara try karo."
    )
