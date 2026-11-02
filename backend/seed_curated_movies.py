"""Seed the MongoDB catalog with the compact curated movie list."""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from .tmdb_service import (
    TMDB_GENRES,
    TMDBError,
    TMDBService,
    get_backdrop_url,
    get_poster_url,
)

TMDB_MOVIE_ID_OFFSET = 1_000_000_000
DATA_PATH = Path(__file__).with_name("curated_movies.json")


def _normalize_title(title):
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_title.casefold())


def _load_curated_titles():
    with DATA_PATH.open(encoding="utf-8") as data_file:
        movies = json.load(data_file)
    if not isinstance(movies, list) or not movies:
        raise ValueError("The curated movie list must be a non-empty JSON array.")
    if len(movies) != 100:
        raise ValueError(f"Expected exactly 100 curated movies; found {len(movies)}.")
    if any(
        not isinstance(movie, dict)
        or not isinstance(movie.get("title"), str)
        or not movie["title"].strip()
        or isinstance(movie.get("year"), bool)
        or not isinstance(movie.get("year"), int)
        for movie in movies
    ):
        raise ValueError("Each curated movie must have a non-empty title and integer year.")
    if len({_normalize_title(movie["title"]) for movie in movies}) != len(movies):
        raise ValueError("The curated movie list contains duplicate titles.")
    return movies


def _resolve_movie(tmdb, curated_movie):
    title = curated_movie["title"]
    expected_year = curated_movie["year"]
    response = tmdb.search_movies(title)
    results = response.get("results", [])
    if not isinstance(results, list):
        raise ValueError(f"TMDB returned an invalid search result for {title!r}.")
    normalized_title = _normalize_title(title)
    matches = [
        result for result in results
        if isinstance(result, dict) and normalized_title in {
            _normalize_title(result.get("title") or ""),
            _normalize_title(result.get("original_title") or "")
        }
        and (result.get("release_date") or "").startswith(str(expected_year))
    ]
    if not matches:
        raise ValueError(
            f"TMDB could not find an exact title and year match for "
            f"{title!r} ({expected_year})."
        )
    return max(matches, key=lambda result: result.get("popularity") or 0)


def _movie_document(result):
    tmdb_id = int(result["id"])
    release_date = result.get("release_date") or ""
    try:
        year = int(release_date[:4]) if release_date else None
    except ValueError:
        year = None
    poster_path = result.get("poster_path")
    backdrop_path = result.get("backdrop_path")
    rating = result.get("vote_average")
    vote_count = result.get("vote_count", 0)
    return {
        "id": TMDB_MOVIE_ID_OFFSET + tmdb_id,
        "tmdb_id": tmdb_id,
        "title": result.get("title") or result.get("original_title") or "Untitled",
        "original_title": result.get("original_title"),
        "overview": result.get("overview") or "",
        "release_date": release_date,
        "year": year,
        "genres": [
            TMDB_GENRES[genre_id]
            for genre_id in result.get("genre_ids", [])
            if genre_id in TMDB_GENRES
        ],
        "popularity": result.get("popularity") or 0,
        "tmdb_rating": rating,
        "tmdb_vote_count": vote_count,
        "vote_average": rating,
        "vote_count": vote_count,
        "poster_path": poster_path,
        "poster_url": get_poster_url(poster_path),
        "backdrop_path": backdrop_path,
        "backdrop_url": get_backdrop_url(backdrop_path),
        "cast": [],
        "directors": [],
        "director": "",
        "keywords": [],
        "data_source": "tmdb",
        "catalog_active": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Resolve the curated 100-film list through TMDB and optionally replace the active catalog."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="upsert the 100 films and deactivate all other catalog movies in MongoDB"
    )
    args = parser.parse_args(argv)

    try:
        curated_movies = _load_curated_titles()
        tmdb = TMDBService()
        resolved_movies = [
            _movie_document(_resolve_movie(tmdb, movie))
            for movie in curated_movies
        ]
        if len({movie["tmdb_id"] for movie in resolved_movies}) != len(resolved_movies):
            raise ValueError("TMDB resolved multiple curated titles to the same movie.")
    except (OSError, ValueError, TMDBError) as error:
        parser.exit(1, f"Catalog preparation failed: {error}\n")

    print(f"Resolved {len(resolved_movies)} of {len(curated_movies)} curated movies.")
    if not args.apply:
        print("Dry run only. Re-run with --apply to update MongoDB.")
        return 0

    from pymongo.errors import PyMongoError

    from .data_store import DatabaseUnavailable, store

    if not store.configured:
        parser.exit(1, "Catalog update failed: MONGODB_URI is not configured.\n")

    try:
        for movie in resolved_movies:
            store.cache_movie(movie, activate=True, preserve_details=True)
        deactivated = store.deactivate_movies_except(
            movie["tmdb_id"] for movie in resolved_movies
        )
    except (DatabaseUnavailable, PyMongoError, ValueError) as error:
        parser.exit(1, f"Catalog update failed: {error}\n")

    print(
        f"Catalog updated: {len(resolved_movies)} curated movies active; "
        f"{deactivated} other movies deactivated."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
