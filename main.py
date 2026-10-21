
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI(title="Apna Map India Navigation")

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
OVERPASS_URL = "https://overpass.private.coffee/api/interpreter"


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
@app.get("/nearby-shops")
async def nearby_shops(lat: float, lng: float):
    # Search within 1500 metres of the user's location.
    query = f"""
    [out:json][timeout:20];
    (
      node(around:1500,{lat},{lng})["shop"];
      way(around:1500,{lat},{lng})["shop"];

      node(around:1500,{lat},{lng})["amenity"="pharmacy"];
      way(around:1500,{lat},{lng})["amenity"="pharmacy"];

      node(around:1500,{lat},{lng})["amenity"="restaurant"];
      way(around:1500,{lat},{lng})["amenity"="restaurant"];

      node(around:1500,{lat},{lng})["amenity"="hospital"];
      way(around:1500,{lat},{lng})["amenity"="hospital"];

      node(around:1500,{lat},{lng})["amenity"="fuel"];
      way(around:1500,{lat},{lng})["amenity"="fuel"];

      node(around:1500,{lat},{lng})["amenity"="atm"];
      way(around:1500,{lat},{lng})["amenity"="atm"];
    );
    out center tags;
    """

    try:
        async with httpx.AsyncClient(
            timeout=35,
            headers={"User-Agent": "ApnaMapIndia/1.0"}
        ) as client:
            response = await client.post(
                OVERPASS_URL,
                data={"data": query}
            )
            response.raise_for_status()

        elements = response.json().get("elements", [])
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

    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Nearby places service busy hai. Baad mein try karo."
    )
        
