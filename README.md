# BAALproject

Continuation of a school project for searching college football players, viewing team/stadium data, and showing stadium maps.

## Setup

```bash
python3 -m pip install -r requirements.txt
python3 BAALv1.7.py
```

If you have your own College Football Data API key, set it before launching:

```bash
export CFBD_API_KEY="your-api-key"
```

The app starts without this variable, but player search and stat lookup require a valid key from CollegeFootballData.com.

## Refactor Notes

The app now separates responsibilities using SOLID-oriented classes:

- `CollegeFootballClient` owns CFBD API calls.
- `CsvTeamRepository` owns local CSV loading and team lookup.
- `ImageService` owns network image fetching and resizing.
- `MapRenderer` owns Folium map creation.
- `PlayerSearchApp` owns Qt widgets and event handling.
