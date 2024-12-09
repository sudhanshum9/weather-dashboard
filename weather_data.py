import os, asyncio
import requests
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import httpx
from io import BytesIO
import matplotlib
from matplotlib import pyplot as plt
from functools import lru_cache

# Use non-interactive Matplotlib backend
matplotlib.use("Agg")

# Initialize FastAPI app
app = FastAPI()

# Enable CORS for frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for simplicity
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Default city list with latitudes and longitudes
cities = [
    {"City": "New York", "Latitude": 40.7128, "Longitude": -74.0060},
    {"City": "Tokyo", "Latitude": 35.6895, "Longitude": 139.6917},
    {"City": "London", "Latitude": 51.5074, "Longitude": -0.1278},
    {"City": "Paris", "Latitude": 48.8566, "Longitude": 2.3522},
    {"City": "Berlin", "Latitude": 52.5200, "Longitude": 13.4050},
    {"City": "Mumbai", "Latitude": 19.0760, "Longitude": 72.8777},
    {"City": "Cape Town", "Latitude": -33.9249, "Longitude": 18.4241},
    {"City": "Moscow", "Latitude": 55.7558, "Longitude": 37.6173},
    {"City": "Rio de Janeiro", "Latitude": -22.9068, "Longitude": -43.1729},
]


# To fetch weather for a single city
async def fetch_weather_for_city(city):
    api_url = f"https://api.open-meteo.com/v1/forecast?latitude={city['Latitude']}&longitude={city['Longitude']}&current_weather=true"
    async with httpx.AsyncClient() as client:
        response = await client.get(api_url)
        if response.status_code == 200:
            data = response.json()
            if "current_weather" in data:
                return {
                    "City": city["City"],
                    "Temperature (C)": data["current_weather"]["temperature"],
                    "Wind Speed (m/s)": data["current_weather"]["windspeed"],
                    "Humidity (%)": data["current_weather"].get(
                        "relative_humidity", "N/A"
                    ),
                }
    return None


# To generate weather data CSV
async def generate_weather_csv_async():
    weather_data = await asyncio.gather(
        *(fetch_weather_for_city(city) for city in cities)
    )
    weather_data = [data for data in weather_data if data]
    df = pd.DataFrame(weather_data)
    df["Temperature (F)"] = df["Temperature (C)"] * 9 / 5 + 32
    df["Wind Speed (mph)"] = df["Wind Speed (m/s)"] * 2.23694
    df.to_csv("weather_data_with_geocoding.csv", index=False)


# Cached function to load weather data from CSV
@lru_cache
def get_weather_data_from_csv():
    return pd.read_csv("weather_data_with_geocoding.csv")


@app.get("/weather-data")
def get_weather_data(
    sort_by: str = Query("Temperature (C)", description="Field to sort by"),
    order: str = Query("desc", description="Sort order: 'asc' or 'desc'"),
    filter_value: str = Query(
        None, description="Value to filter by (applies to 'City')"
    ),
):
    try:
        # Load weather data
        df = pd.read_csv("weather_data_with_geocoding.csv")

        # Replace invalid numeric values (NaN, inf) with 0 or default valid values
        df.replace([float("inf"), float("-inf")], None, inplace=True)
        df.fillna(
            {"Temperature (C)": 0, "Wind Speed (m/s)": 0, "Humidity (%)": "N/A"},
            inplace=True,
        )

        # Apply filtering based on 'City'
        if filter_value:
            df = df[df["City"].astype(str).str.contains(filter_value, case=False)]

        # Apply sorting
        df = df.sort_values(by=sort_by, ascending=(order == "asc"))

        # Convert DataFrame to JSON-friendly format
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error processing weather data: {str(e)}"
        )


@app.post("/add-city")
async def add_city(city: str):
    geocoding_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}"
    response = requests.get(geocoding_url)
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Geocoding API request failed.")

    data = response.json()
    if "results" not in data or not data["results"]:
        raise HTTPException(status_code=404, detail="City not found.")

    # Get the first result
    result = data["results"][0]
    new_city = {
        "City": city,
        "Latitude": result["latitude"],
        "Longitude": result["longitude"],
    }
    cities.append(new_city)

    # Fetch weather data for the new city (await properly)
    city_weather = await fetch_weather_for_city(new_city)
    if not city_weather:
        raise HTTPException(
            status_code=500, detail="Failed to fetch weather data for the new city."
        )

    # Append the new city's weather data to the CSV
    csv_file = "weather_data_with_geocoding.csv"
    try:
        # Check if the file exists and has content
        if os.path.exists(csv_file) and os.path.getsize(csv_file) > 0:
            df = pd.read_csv(csv_file)
        else:
            df = pd.DataFrame()

        # Append new data
        df = pd.concat([df, pd.DataFrame([city_weather])], ignore_index=True)

        # Recalculate additional fields
        df["Temperature (F)"] = df["Temperature (C)"] * 9 / 5 + 32
        df["Wind Speed (mph)"] = df["Wind Speed (m/s)"] * 2.23694
        df.to_csv(csv_file, index=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating CSV: {str(e)}")

    return {"message": f"City '{city}' added successfully."}


@app.get("/download/weather-data")
def download_csv():
    try:
        return FileResponse(
            "weather_data_with_geocoding.csv",
            media_type="text/csv",
            filename="weather_data_with_geocoding.csv",
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Weather data CSV not found.")


@app.get("/plot/temperature")
async def plot_temperature():
    csv_file = "weather_data_with_geocoding.csv"

    # Check if file exists and is not empty
    if not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0:
        # Fetch data for default cities and populate the file
        weather_data = await asyncio.gather(
            *(fetch_weather_for_city(city) for city in cities)
        )
        weather_data = [data for data in weather_data if data]

        if not weather_data:
            raise HTTPException(
                status_code=500, detail="Failed to fetch default weather data."
            )

        df = pd.DataFrame(weather_data)
        df["Temperature (F)"] = df["Temperature (C)"] * 9 / 5 + 32
        df["Wind Speed (mph)"] = df["Wind Speed (m/s)"] * 2.23694
        df.to_csv(csv_file, index=False)

    try:
        df = pd.read_csv(csv_file)
        plt.figure(figsize=(12, 6))
        plt.bar(df["City"], df["Temperature (C)"], color="blue", alpha=0.7)
        plt.axhline(
            0, color="gray", linestyle="--", linewidth=0.8
        )  # Add reference line for 0°C
        plt.title("Temperature (C) in Various Cities")
        plt.xlabel("City")
        plt.ylabel("Temperature (C)")
        plt.xticks(rotation=45)
        plt.tight_layout()

        buf = BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()
        return StreamingResponse(buf, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating graph: {str(e)}")


@app.get("/plot/combined")
async def plot_combined():
    csv_file = "weather_data_with_geocoding.csv"

    # Check if file exists and is not empty
    if not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0:
        # Fetch data for default cities and populate the file
        weather_data = await asyncio.gather(
            *(fetch_weather_for_city(city) for city in cities)
        )
        weather_data = [data for data in weather_data if data]

        if not weather_data:
            raise HTTPException(
                status_code=500, detail="Failed to fetch default weather data."
            )

        df = pd.DataFrame(weather_data)
        df["Temperature (F)"] = df["Temperature (C)"] * 9 / 5 + 32
        df["Wind Speed (mph)"] = df["Wind Speed (m/s)"] * 2.23694
        df.to_csv(csv_file, index=False)

    try:
        df = pd.read_csv(csv_file)
        fig, ax1 = plt.subplots(figsize=(12, 6))

        ax1.set_xlabel("City")
        ax1.set_ylabel("Temperature (C)", color="blue")
        ax1.plot(
            df["City"],
            df["Temperature (C)"],
            marker="o",
            color="blue",
            label="Temperature (C)",
        )
        ax1.tick_params(axis="y", labelcolor="blue")

        ax2 = ax1.twinx()
        ax2.set_ylabel("Wind Speed (m/s)", color="green")
        ax2.plot(
            df["City"],
            df["Wind Speed (m/s)"],
            marker="s",
            color="green",
            label="Wind Speed (m/s)",
        )
        ax2.tick_params(axis="y", labelcolor="green")

        plt.title("Combined Temperature and Wind Speed in Various Cities")
        fig.tight_layout()

        buf = BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()
        return StreamingResponse(buf, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating graph: {str(e)}")
