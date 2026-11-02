"""Small, reusable client for TMDB API v3."""

from pathlib import Path
import os
import re
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"


class TMDBError(Exception):
    """Base exception for safe TMDB service errors."""


class TMDBConfigurationError(TMDBError):
    pass


class TMDBConnectionError(TMDBError):
    pass


class TMDBTimeoutError(TMDBConnectionError):
    pass


class TMDBAPIError(TMDBError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"TMDB returned HTTP {status_code}.")


class TMDBResponseError(TMDBError):
    pass


def _image_url(path, size):
    if not isinstance(path, str) or not path.strip():
        return None
    clean_path = quote(path.strip().lstrip("/"), safe="/")
    return f"{TMDB_IMAGE_BASE}/{size}/{clean_path}"


def get_poster_url(poster_path):
    return _image_url(poster_path, "w500")


def get_backdrop_url(backdrop_path):
    return _image_url(backdrop_path, "w1280")


class TMDBService:
    def __init__(self, api_key=None, timeout=10, session=None):
        self._api_key = api_key or os.environ.get("TMDB_API_KEY")
        self._timeout = timeout
        self._session = session or requests.Session()
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

    def _request(self, endpoint, params=None):
        if not self._api_key:
            raise TMDBConfigurationError("TMDB_API_KEY is not configured.")

        request_params = {"api_key": self._api_key}
        if params:
            request_params.update(params)

        try:
            response = self._session.get(
                f"{TMDB_API_BASE}/{endpoint.lstrip('/')}",
                params=request_params,
                timeout=self._timeout
            )
        except requests.Timeout:
            raise TMDBTimeoutError("The TMDB request timed out.") from None
        except requests.ConnectionError:
            raise TMDBConnectionError("Could not connect to TMDB.") from None
        except requests.RequestException:
            raise TMDBConnectionError("The TMDB request failed.") from None

        if not response.ok:
            raise TMDBAPIError(response.status_code)

        try:
            data = response.json()
        except (requests.exceptions.JSONDecodeError, ValueError):
            raise TMDBResponseError("TMDB returned invalid JSON.") from None
        if not isinstance(data, dict):
            raise TMDBResponseError("TMDB returned an unexpected response format.")
        return data

    @staticmethod
    def _validate_movie_id(tmdb_id):
        if isinstance(tmdb_id, bool) or not str(tmdb_id).isdigit() or int(tmdb_id) <= 0:
            raise ValueError("tmdb_id must be a positive integer")
        return int(tmdb_id)

    def search_movies(self, query, page=1):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 500:
            raise ValueError("page must be an integer between 1 and 500")
        return self._request("search/movie", {"query": query.strip(), "page": page})

    def discover_movies(self, page=1, region="IN", sort_by="popularity.desc", extra_params=None):
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 500:
            raise ValueError("page must be an integer between 1 and 500")
        if region and (not isinstance(region, str) or not re.fullmatch(r"[A-Za-z]{2}", region)):
            raise ValueError("region must be a two-letter country code")
        valid_sort_options = {
            f"{field}.{direction}"
            for field in (
                "original_title", "popularity", "revenue", "primary_release_date",
                "title", "vote_average", "vote_count"
            )
            for direction in ("asc", "desc")
        }
        if sort_by not in valid_sort_options:
            raise ValueError("sort_by is not a supported TMDB movie sort option")
        params = {"page": page, "sort_by": sort_by}
        if region:
            params["region"] = region.upper()
        if extra_params and isinstance(extra_params, dict):
            params.update(extra_params)
        return self._request("discover/movie", params)

    def get_movie_details(self, tmdb_id, append_to_response=None):
        movie_id = self._validate_movie_id(tmdb_id)
        params = {}
        if append_to_response:
            params["append_to_response"] = append_to_response
        return self._request(f"movie/{movie_id}", params if params else None)

    def get_movie_credits(self, tmdb_id):
        movie_id = self._validate_movie_id(tmdb_id)
        return self._request(f"movie/{movie_id}/credits")

    def get_movie_videos(self, tmdb_id):
        movie_id = self._validate_movie_id(tmdb_id)
        return self._request(f"movie/{movie_id}/videos")

    def get_movie_watch_providers(self, tmdb_id, country="IN"):
        movie_id = self._validate_movie_id(tmdb_id)
        if not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}", country):
            raise ValueError("country must be a two-letter country code")
        region = country.upper()
        data = self._request(f"movie/{movie_id}/watch/providers")
        providers = data.get("results", {}).get(region, {})
        return {"tmdb_id": movie_id, "country": region, **providers}


def normalize_movie_details(data):
    if not isinstance(data, dict):
        raise TMDBResponseError("Movie details must be a TMDB response object.")

    return {
        "tmdb_id": data.get("id"),
        "imdb_id": data.get("imdb_id"),
        "title": data.get("title"),
        "original_title": data.get("original_title"),
        "overview": data.get("overview"),
        "release_date": data.get("release_date"),
        "runtime": data.get("runtime"),
        "genres": [genre.get("name") for genre in data.get("genres", []) if genre.get("name")],
        "original_language": data.get("original_language"),
        "origin_country": data.get("origin_country", []),
        "production_companies": [
            {
                "id": company.get("id"),
                "name": company.get("name"),
                "origin_country": company.get("origin_country")
            }
            for company in data.get("production_companies", [])
        ],
        "production_countries": [
            {"iso_3166_1": country.get("iso_3166_1"), "name": country.get("name")}
            for country in data.get("production_countries", [])
        ],
        "spoken_languages": [
            {
                "english_name": language.get("english_name"),
                "name": language.get("name"),
                "iso_639_1": language.get("iso_639_1")
            }
            for language in data.get("spoken_languages", [])
        ],
        "tagline": data.get("tagline"),
        "status": data.get("status"),
        "popularity": data.get("popularity"),
        "vote_average": data.get("vote_average"),
        "vote_count": data.get("vote_count"),
        "poster_path": data.get("poster_path"),
        "poster_url": get_poster_url(data.get("poster_path")),
        "backdrop_path": data.get("backdrop_path"),
        "backdrop_url": get_backdrop_url(data.get("backdrop_path")),
        "homepage": data.get("homepage")
    }


def normalize_movie_credits(data, cast_limit=15):
    if not isinstance(data, dict):
        raise TMDBResponseError("Movie credits must be a TMDB response object.")
    if isinstance(cast_limit, bool) or not isinstance(cast_limit, int) or cast_limit < 0:
        raise ValueError("cast_limit must be a non-negative integer")

    cast = [
        {
            "id": person.get("id"),
            "name": person.get("name"),
            "character": person.get("character"),
            "order": person.get("order"),
            "profile_path": person.get("profile_path"),
            "profile_url": get_poster_url(person.get("profile_path"))
        }
        for person in data.get("cast", [])[:cast_limit]
    ]
    crew = [
        {
            "id": person.get("id"),
            "name": person.get("name"),
            "department": person.get("department"),
            "job": person.get("job"),
            "profile_path": person.get("profile_path"),
            "profile_url": get_poster_url(person.get("profile_path"))
        }
        for person in data.get("crew", [])
        if person.get("job") in {
            "Director", "Producer", "Executive Producer", "Screenplay", "Writer",
            "Director of Photography", "Original Music Composer", "Editor"
        }
    ]
    directors = [person["name"] for person in crew if person["job"] == "Director"]
    return {"cast": cast, "directors": directors, "crew": crew}


def select_youtube_trailer(videos_response):
    if not isinstance(videos_response, dict):
        raise TMDBResponseError("Movie videos must be a TMDB response object.")

    trailers = [
        video for video in videos_response.get("results", [])
        if video.get("site", "").casefold() == "youtube"
        and video.get("type", "").casefold() == "trailer"
        and video.get("key")
    ]
    if not trailers:
        return None
    selected = next((video for video in trailers if video.get("official") is True), trailers[0])
    return {
        "name": selected.get("name"),
        "key": selected["key"],
        "site": "YouTube",
        "type": "Trailer",
        "official": bool(selected.get("official", False))
    }


_default_client = None


def _client():
    global _default_client
    if _default_client is None:
        _default_client = TMDBService()
    return _default_client


def search_movies(query, page=1):
    return _client().search_movies(query, page)


def get_movie_details(tmdb_id, append_to_response=None):
    return _client().get_movie_details(tmdb_id, append_to_response)


def get_movie_credits(tmdb_id):
    return _client().get_movie_credits(tmdb_id)


def get_movie_videos(tmdb_id):
    return _client().get_movie_videos(tmdb_id)


def get_movie_watch_providers(tmdb_id, country="IN"):
    return _client().get_movie_watch_providers(tmdb_id, country)


def discover_movies(page=1, region="IN", sort_by="popularity.desc", extra_params=None):
    return _client().discover_movies(page, region, sort_by, extra_params)