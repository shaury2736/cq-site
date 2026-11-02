# Compact movie catalog

The repository includes a compact list of 100 well-known films in
`backend/curated_movies.json`. It stores only each title and release year; movie
details and poster URLs are resolved from TMDB when you seed the catalog.

To preview the TMDB matches from the repository root:

```powershell
python -m backend.seed_curated_movies
```

Set `TMDB_API_KEY` before running the command. To apply the catalog, also set
`MONGODB_URI` to the MongoDB database used by the app, then run:

```powershell
python -m backend.seed_curated_movies --apply
```

The apply operation upserts the 100 films and marks other catalog movies
inactive; it does not permanently delete them or remove user ratings,
watchlists, or reviews. The curated titles are reactivated if they were
previously inactive, and existing detailed movie data is retained. Deactivated
records still occupy MongoDB storage; this operation reduces the active
catalog, not the database's physical size.

Run the seed before starting the deployed app, or restart the app afterward so
its in-memory search and recommendation indexes reload from MongoDB.

The movie catalog is stored in MongoDB, not in the Vercel or Render deployment
bundle. The JSON manifest is only a few kilobytes; this reduces the active
catalog size, but does not remove the app's MongoDB requirement or guarantee a
specific hosting limit. Production also requires `SECRET_KEY` and
`MONGODB_URI`; set them as deployment environment variables, not in source.
The repository's local virtual environment is ignored by Git and should not be
uploaded as part of the deployment.
