"""MongoDB-backed users, ratings, reviews, and watchlists for Cinema Quotient."""

from datetime import datetime, timedelta, timezone
import logging
import os
import re
import secrets
from dotenv import load_dotenv

load_dotenv()

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from werkzeug.security import check_password_hash

TMDB_MOVIE_ID_OFFSET = 1_000_000_000


class DatabaseUnavailable(Exception):
    pass


class DataStore:
    def __init__(self):
        mongo_uri = os.environ.get("MONGODB_URI")
        self.client = None
        self.db = None
        if mongo_uri and ("<" in mongo_uri or ">" in mongo_uri):
            if os.environ.get("APP_ENV") == "production":
                raise DatabaseUnavailable("Replace the MongoDB template URI before production startup")
            logging.getLogger(__name__).warning(
                "MongoDB template URI detected; account persistence is disabled in development."
            )
            mongo_uri = None
        if mongo_uri:
            try:
                self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000, tz_aware=True)
                database_name = os.environ.get("MONGODB_DATABASE", "cinema_quotient")
                self.db = self.client[database_name]
                self.client.admin.command("ping")
                self._create_indexes()
            except PyMongoError as error:
                if os.environ.get("APP_ENV") == "production":
                    raise DatabaseUnavailable("Could not connect to MongoDB Atlas") from None
                if self.client:
                    self.client.close()
                self.client = None
                self.db = None
                logging.getLogger(__name__).warning(
                    "MongoDB is unavailable; account endpoints will return service unavailable."
                )

    @property
    def configured(self):
        return self.db is not None

    def _require_db(self):
        if self.db is None:
            raise DatabaseUnavailable("MongoDB is not configured")
        return self.db

    def _create_indexes(self):
        self.db.users.create_index([("user_id", ASCENDING)], unique=True)
        self.db.users.create_index([("email", ASCENDING)], unique=True)
        self.db.users.create_index([("google_sub", ASCENDING)], unique=True, sparse=True)
        self.db.ratings.create_index(
            [("user_id", ASCENDING), ("movie_id", ASCENDING)], unique=True
        )
        self.db.ratings.create_index([("movie_id", ASCENDING)])
        self.db.watchlists.create_index(
            [("user_id", ASCENDING), ("movie_id", ASCENDING)], unique=True
        )
        self.db.movies.create_index([("id", ASCENDING)], unique=True)
        self.db.movies.create_index([("tmdb_id", ASCENDING)], unique=True)
        self.db.movies.create_index([("catalog_active", ASCENDING)])
        self.db.movies.create_index([("popularity", -1)])
        self.db.movies.create_index([("tmdb_rating", -1)])
        self.db.movies.create_index([("genres", ASCENDING)])
        self.db.movies.create_index([("original_language", ASCENDING)])
        self.db.movies.create_index([("origin_country", ASCENDING)])
        self.db.movies.create_index([("catalog_active", ASCENDING), ("popularity", -1)])
        self.db.movies.create_index([("catalog_active", ASCENDING), ("tmdb_rating", -1)])
        self.db.movies.create_index([("directors", ASCENDING)])
        self.db.movies.create_index([("cast.name", ASCENDING)])
        self.db.movies.create_index([("release_date", -1)])
        try:
            self.db.movies.create_index([
                ("title", "text"),
                ("original_title", "text"),
                ("directors", "text"),
                ("cast.name", "text"),
                ("genres", "text"),
                ("overview", "text")
            ], name="movies_full_text", default_language="english")
        except PyMongoError as idx_err:
            logging.getLogger(__name__).warning("Text index creation skipped: %s", idx_err)
        self.db.pending_registrations.create_index([("email", ASCENDING)], unique=True)
        self.db.pending_registrations.create_index("expires_at", expireAfterSeconds=0)
        latest_user = self.db.users.find_one(sort=[("user_id", -1)])
        self.db.counters.update_one(
            {"_id": "user_id"},
            {"$max": {"value": latest_user["user_id"] if latest_user else 0}},
            upsert=True
        )
        now = datetime.now(timezone.utc)
        self.db.movies.update_many(
            {"tmdb_id": {"$exists": True}},
            [{"$set": {
                "tmdb_rating": {"$ifNull": ["$tmdb_rating", "$vote_average"]},
                "tmdb_vote_count": {"$ifNull": ["$tmdb_vote_count", "$vote_count"]},
                "imdb_rating": None,
                "data_source": {"$ifNull": ["$data_source", "tmdb"]},
                "last_synced_at": {"$ifNull": ["$last_synced_at", now]},
                "watch_providers": {"$ifNull": ["$watch_providers", {}]},
                "watch_provider_country": {"$ifNull": ["$watch_provider_country", "IN"]},
                "admin_overrides": {"$ifNull": ["$admin_overrides", {}]},
                "catalog_active": {"$ifNull": ["$catalog_active", True]}
            }}]
        )

    @staticmethod
    def _clean_user(user):
        if user:
            user.pop("_id", None)
        return user

    def _next_user_id(self):
        counter = self._require_db().counters.find_one_and_update(
            {"_id": "user_id"}, {"$inc": {"value": 1}},
            upsert=True, return_document=ReturnDocument.AFTER
        )
        return counter["value"]

    # ---------------------------------------------------------------------------
    # User Authentication & Profile Methods
    # ---------------------------------------------------------------------------

    def get_user_by_id(self, user_id):
        if not user_id:
            return None
        try:
            user = self._require_db().users.find_one({"user_id": int(user_id)})
        except ValueError:
            return None
        return self._clean_user(user)

    def get_user_by_email(self, email):
        if not email:
            return None
        email_clean = email.strip().lower()
        user = self._require_db().users.find_one({"email": email_clean})
        return self._clean_user(user)

    def get_user_by_google_sub(self, google_sub):
        if not google_sub:
            return None
        user = self._require_db().users.find_one({"google_sub": google_sub})
        return self._clean_user(user)

    def authenticate_user(self, email, password):
        user = self.get_user_by_email(email)
        if user and user.get("email_verified") and user.get("password_hash"):
            if check_password_hash(user["password_hash"], password):
                return user
        return None

    @staticmethod
    def _user_document(user_id, name, email, password_hash, google_sub=None, picture=None):
        now = datetime.now(timezone.utc)
        methods = ["email"] + (["google"] if google_sub else [])
        avatar = picture or f"https://api.dicebear.com/7.x/avataaars/svg?seed={email}"
        user = {
            "user_id": user_id,
            "name": name,
            "email": email,
            "password_hash": password_hash,
            "profile_image": picture,
            "role": "user",
            "auth_provider": "both" if google_sub else "email",
            "auth_methods": methods,
            "avatar": avatar,
            "email_verified": True,
            "favorite_genres": ["Sci-Fi", "Action"],
            "created_at": now,
            "updated_at": now
        }
        if google_sub:
            user["google_sub"] = google_sub
        return user

    def issue_registration_otp(
        self, email, name, password_hash, otp_hash, google_sub=None, picture=None
    ):
        db = self._require_db()
        email = email.strip().lower()
        if db.users.find_one({"email": email}, {"_id": 1}):
            return "An account with this email already exists."

        now = datetime.now(timezone.utc)
        challenge = db.pending_registrations.find_one({"email": email})
        if challenge and challenge["resend_after"] > now:
            return "Please wait before requesting another verification code."

        document = {
            "email": email,
            "name": name.strip(),
            "password_hash": password_hash,
            "google_sub": google_sub,
            "profile_image": picture,
            "otp_hash": otp_hash,
            "attempts": 0,
            "expires_at": now + timedelta(minutes=10),
            "resend_after": now + timedelta(seconds=60),
            "created_at": now
        }
        try:
            if challenge:
                result = db.pending_registrations.update_one(
                    {"email": email, "resend_after": {"$lte": now}},
                    {"$set": {key: value for key, value in document.items() if key != "email"}}
                )
                if result.matched_count != 1:
                    return "Please wait before requesting another verification code."
            else:
                db.pending_registrations.insert_one(document)
        except DuplicateKeyError:
            return "Please wait before requesting another verification code."
        except PyMongoError as error:
            raise DatabaseUnavailable("Could not save registration verification") from error
        return None

    def cancel_pending_registration(self, email, otp_hash):
        self._require_db().pending_registrations.delete_one({
            "email": email.strip().lower(), "otp_hash": otp_hash
        })

    def resend_registration_otp(self, email, otp_hash):
        db = self._require_db()
        email = email.strip().lower()
        now = datetime.now(timezone.utc)
        challenge = db.pending_registrations.find_one({"email": email})
        if not challenge or challenge["expires_at"] <= now:
            if challenge:
                db.pending_registrations.delete_one({"email": email})
            return None, "Registration expired. Please start again."
        if challenge["resend_after"] > now:
            return None, "Please wait before requesting another verification code."

        result = db.pending_registrations.update_one(
            {"email": email, "resend_after": {"$lte": now}},
            {"$set": {
                "otp_hash": otp_hash,
                "attempts": 0,
                "expires_at": now + timedelta(minutes=10),
                "resend_after": now + timedelta(seconds=60)
            }}
        )
        if result.modified_count != 1:
            return None, "Please wait before requesting another verification code."
        return challenge, None

    def verify_registration_otp(self, email, otp_hash):
        db = self._require_db()
        email = email.strip().lower()
        now = datetime.now(timezone.utc)
        challenge = db.pending_registrations.find_one({"email": email})
        if not challenge or challenge["expires_at"] <= now:
            if challenge:
                db.pending_registrations.delete_one({"email": email})
            return None, "Verification code expired. Request a new one."
        if challenge["attempts"] >= 5:
            return None, "Too many incorrect codes. Request a new verification code."
        if not secrets.compare_digest(challenge["otp_hash"], otp_hash):
            db.pending_registrations.update_one(
                {"email": email, "attempts": {"$lt": 5}}, {"$inc": {"attempts": 1}}
            )
            if challenge["attempts"] >= 4:
                return None, "Too many incorrect codes. Request a new verification code."
            return None, "The verification code is incorrect."

        consumed = db.pending_registrations.find_one_and_delete({
            "email": email,
            "otp_hash": otp_hash,
            "attempts": {"$lt": 5},
            "expires_at": {"$gt": now}
        })
        if not consumed:
            return None, "Verification code is no longer valid. Request a new one."

        document = self._user_document(
            self._next_user_id(), consumed["name"], email,
            consumed["password_hash"], consumed.get("google_sub"),
            consumed.get("profile_image")
        )
        try:
            db.users.insert_one(document)
        except DuplicateKeyError:
            return None, "An account with this email or Google account already exists."
        return self.get_user_by_id(document["user_id"]), None

    def link_google_account(self, email, google_sub, name=None, picture=None):
        db = self._require_db()
        email = email.strip().lower()
        user = db.users.find_one({"email": email, "email_verified": True})
        if not user:
            return None, "No verified account exists for this email."
        if user.get("role") == "admin":
            return None, "Administrator accounts must use administrator sign-in."
        linked = db.users.find_one({"google_sub": google_sub, "user_id": {"$ne": user["user_id"]}})
        if linked:
            return None, "This Google account is already linked to another account."
        current_sub = user.get("google_sub")
        if current_sub and current_sub != google_sub:
            return None, "A different Google account is already linked to this email."

        update = {
            "google_sub": google_sub,
            "auth_provider": "both" if user.get("password_hash") else "google",
            "updated_at": datetime.now(timezone.utc)
        }
        if picture:
            update.update({"profile_image": picture, "avatar": picture})
        if name and not user.get("name"):
            update["name"] = name
        try:
            db.users.update_one({"user_id": user["user_id"]}, {
                "$set": update,
                "$addToSet": {"auth_methods": "google"}
            })
        except DuplicateKeyError:
            return None, "This Google account is already linked to another account."
        return self.get_user_by_id(user["user_id"]), None

    def provision_admin(self, email, password_hash):
        db = self._require_db()
        email = email.strip().lower()
        existing = db.users.find_one({"email": email})
        now = datetime.now(timezone.utc)
        if existing:
            db.users.update_one({"user_id": existing["user_id"]}, {"$set": {
                "role": "admin", "password_hash": password_hash,
                "email_verified": True, "updated_at": now
            }, "$addToSet": {"auth_methods": "email"}})
            return
        document = self._user_document(
            self._next_user_id(), "Cinema Quotient Admin", email, password_hash
        )
        document["role"] = "admin"
        try:
            db.users.insert_one(document)
        except DuplicateKeyError:
            db.users.update_one({"email": email}, {"$set": {
                "role": "admin", "password_hash": password_hash,
                "email_verified": True, "updated_at": now
            }})

    def get_all_users(self, search=None):
        """For Admin Portal: returns all users with summary metrics."""
        result = []
        db = self._require_db()
        query = {}
        if search:
            query["$or"] = [
                {"name": {"$regex": re.escape(search), "$options": "i"}},
                {"email": {"$regex": re.escape(search), "$options": "i"}}
            ]
            if str(search).isdigit():
                query["$or"].append({"user_id": int(search)})
        for user in db.users.find(query).sort("user_id", ASCENDING):
            u_id = user["user_id"]
            u_ratings_count = db.ratings.count_documents({"user_id": u_id})
            u_watchlist_count = db.watchlists.count_documents({"user_id": u_id})
            item = self._clean_user(user)
            item.pop("password_hash", None)
            item.pop("google_sub", None)
            item["ratings_count"] = u_ratings_count
            item["watchlist_count"] = u_watchlist_count
            result.append(item)
        return result

    def get_admin_user(self, user_id):
        user = self.get_user_by_id(user_id)
        if not user:
            return None
        user.pop("password_hash", None)
        user.pop("google_sub", None)
        return user

    def update_user_profile(self, user_id, changes):
        allowed_fields = {"name", "favorite_genres"}
        updates = {key: value for key, value in changes.items() if key in allowed_fields}
        if not updates:
            return self.get_admin_user(user_id)
        if "name" in updates:
            if not isinstance(updates["name"], str) or not updates["name"].strip() or len(updates["name"].strip()) > 120:
                raise ValueError("Name must contain 1 to 120 characters.")
            updates["name"] = updates["name"].strip()
        if "favorite_genres" in updates:
            genres = updates["favorite_genres"]
            if not isinstance(genres, list) or any(not isinstance(genre, str) for genre in genres):
                raise ValueError("Favorite genres must be a list of strings.")
            updates["favorite_genres"] = [genre.strip() for genre in genres if genre.strip()]
        updates["updated_at"] = datetime.now(timezone.utc)
        self._require_db().users.update_one({"user_id": int(user_id)}, {"$set": updates})
        return self.get_admin_user(user_id)

    def delete_user(self, user_id):
        """For Admin Portal: delete user and clean up all their ratings & watchlist."""
        db = self._require_db()
        user_id = int(user_id)
        if db.users.delete_one({"user_id": user_id}).deleted_count != 1:
            return False
        db.ratings.delete_many({"user_id": user_id})
        db.watchlists.delete_many({"user_id": user_id})
        return True

    def update_favorite_genres(self, user_id, genres):
        result = self._require_db().users.update_one(
            {"user_id": int(user_id)},
            {"$set": {"favorite_genres": genres, "updated_at": datetime.now(timezone.utc)}}
        )
        return genres if result.matched_count else []

    # ---------------------------------------------------------------------------
    # User Ratings & Reviews Methods
    # ---------------------------------------------------------------------------

    def add_rating(self, user_id, movie_id, rating, review=""):
        """Instant user rating/review submission."""
        user_id = int(user_id)
        movie_id = int(movie_id)
        rating_val = max(1.0, min(5.0, float(rating)))
        data = {
            "rating": rating_val,
            "review": review.strip() if review else "",
            "updated_at": datetime.now(timezone.utc)
        }
        self._require_db().ratings.update_one(
            {"user_id": user_id, "movie_id": movie_id},
            {"$set": data, "$setOnInsert": {"user_id": user_id, "movie_id": movie_id}},
            upsert=True
        )
        return self.get_user_ratings(user_id)

    def remove_rating(self, user_id, movie_id):
        user_id = int(user_id)
        movie_id = int(movie_id)
        self._require_db().ratings.delete_one({"user_id": user_id, "movie_id": movie_id})
        return self.get_user_ratings(user_id)

    def get_user_ratings(self, user_id):
        """Returns dict of {movie_id: rating_float} for recommendation engine & card badges."""
        user_id = int(user_id)
        return {
            item["movie_id"]: item["rating"]
            for item in self._require_db().ratings.find(
                {"user_id": user_id}, {"movie_id": 1, "rating": 1, "_id": 0}
            )
        }

    def get_rating_events(self):
        return list(self._require_db().ratings.find(
            {}, {"user_id": 1, "movie_id": 1, "rating": 1, "_id": 0}
        ))

    def get_rating_for_movie(self, user_id, movie_id):
        if not user_id:
            return None
        data = self._require_db().ratings.find_one(
            {"user_id": int(user_id), "movie_id": int(movie_id)}, {"rating": 1}
        )
        return data["rating"] if data else None

    def get_user_rating_history(self, user_id):
        user_id = int(user_id)
        history = []
        ratings = self._require_db().ratings.find({"user_id": user_id}).sort("updated_at", -1)
        for data in ratings:
            m = self.get_movie_by_id(data["movie_id"])
            if m:
                history.append({
                    "movie_id": m["id"],
                    "title": m["title"],
                    "year": m["year"],
                    "poster_url": m["poster_url"],
                    "genres": m["genres"],
                    "rating": data["rating"],
                    "review": data["review"],
                    "updated_at": data["updated_at"]
                })
        return history

    def get_movie_ratings(self, movie_id):
        """Get all user ratings and reviews for a specific movie."""
        movie_id = int(movie_id)
        results = []
        for data in self._require_db().ratings.find({"movie_id": movie_id}):
            user = self.get_user_by_id(data["user_id"])
            results.append({
                "user_id": data["user_id"],
                "user_name": user["name"] if user else f"User #{data['user_id']}",
                "user_email": user["email"] if user else "",
                "user_avatar": user.get("avatar", "") if user else "",
                "rating": data["rating"],
                "review": data["review"],
                "updated_at": data["updated_at"]
            })
        return results

    def get_all_ratings(self):
        """For Admin Portal: returns all ratings across all users & movies."""
        results = []
        for data in self._require_db().ratings.find():
            u_id = data["user_id"]
            m_id = data["movie_id"]
            user = self.get_user_by_id(u_id)
            movie = self.get_movie_by_id(m_id)
            if user and movie:
                results.append({
                    "user_id": u_id,
                    "user_name": user["name"],
                    "user_email": user["email"],
                    "movie_id": m_id,
                    "movie_title": movie["title"],
                    "movie_poster": movie["poster_url"],
                    "rating": data["rating"],
                    "review": data["review"],
                    "updated_at": data["updated_at"]
                })
        return results

    def admin_update_rating(self, user_id, movie_id, new_rating, new_review):
        """For Admin Portal: alter any user's rating or review."""
        u_id = int(user_id)
        m_id = int(movie_id)
        result = self._require_db().ratings.update_one(
            {"user_id": u_id, "movie_id": m_id},
            {"$set": {
                "rating": max(1.0, min(5.0, float(new_rating))),
                "review": new_review.strip() if new_review else "",
                "updated_at": datetime.now(timezone.utc)
            }}
        )
        return result.matched_count == 1

    def admin_delete_rating(self, user_id, movie_id):
        """For Admin Portal: delete any user's rating."""
        u_id = int(user_id)
        m_id = int(movie_id)
        result = self._require_db().ratings.delete_one({"user_id": u_id, "movie_id": m_id})
        return result.deleted_count == 1

    # ---------------------------------------------------------------------------
    # Watchlist Methods
    # ---------------------------------------------------------------------------

    def add_to_watchlist(self, user_id, movie_id):
        user_id = int(user_id)
        movie_id = int(movie_id)
        self._require_db().watchlists.update_one(
            {"user_id": user_id, "movie_id": movie_id},
            {"$setOnInsert": {
                "user_id": user_id, "movie_id": movie_id,
                "created_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )
        return self.get_watchlist(user_id)

    def remove_from_watchlist(self, user_id, movie_id):
        user_id = int(user_id)
        self._require_db().watchlists.delete_one(
            {"user_id": user_id, "movie_id": int(movie_id)}
        )
        return self.get_watchlist(user_id)

    def get_watchlist(self, user_id):
        user_id = int(user_id)
        return [item["movie_id"] for item in self._require_db().watchlists.find(
            {"user_id": user_id}, {"movie_id": 1, "_id": 0}
        )]

    def is_in_watchlist(self, user_id, movie_id):
        if not user_id:
            return False
        return self._require_db().watchlists.find_one({
            "user_id": int(user_id), "movie_id": int(movie_id)
        }, {"_id": 1}) is not None

    # ---------------------------------------------------------------------------
    # Movie Catalog Admin & Data Methods
    # ---------------------------------------------------------------------------

    def get_all_movies(
        self, search=None, genre=None, year=None, min_rating=None,
        sort_by="popular", include_inactive=False
    ):
        db = self._require_db()
        query = {"tmdb_id": {"$exists": True}}
        if not include_inactive:
            query["catalog_active"] = {"$ne": False}
        if search:
            expression = {"$regex": re.escape(search), "$options": "i"}
            query["$or"] = [
                {"title": expression}, {"original_title": expression},
                {"directors": expression}, {"cast.name": expression}
            ]
        if genre and genre.casefold() != "all":
            query["genres"] = {"$regex": f"^{re.escape(genre)}$", "$options": "i"}
        if year is not None:
            query["release_date"] = {"$regex": f"^{int(year)}"}
        if min_rating is not None:
            query["tmdb_rating"] = {"$gte": float(min_rating)}

        sort_fields = {
            "popular": ("popularity", -1),
            "rating": ("tmdb_rating", -1),
            "year": ("release_date", -1),
            "title": ("title", 1)
        }
        sort_field, direction = sort_fields.get(sort_by, sort_fields["popular"])
        return [
            self._clean_user(movie)
            for movie in db.movies.find(query).sort(sort_field, direction)
        ]

    def get_movie_by_id(self, movie_id):
        movie_id = int(movie_id)
        movie = self._require_db().movies.find_one({
            "$or": [{"id": movie_id}, {"tmdb_id": movie_id}]
        })
        return self._clean_user(movie)

    def get_movie_by_tmdb_id(self, tmdb_id):
        movie = self._require_db().movies.find_one({"tmdb_id": int(tmdb_id)})
        return self._clean_user(movie)

    def get_all_existing_tmdb_ids(self):
        """Returns set of all existing tmdb_ids in the collection."""
        return set(
            doc["tmdb_id"]
            for doc in self._require_db().movies.find({}, {"tmdb_id": 1, "_id": 0})
            if "tmdb_id" in doc
        )

    def cache_movie(self, movie, fetch_after=True):
        """Upsert a TMDB movie while reapplying any explicit admin overrides."""
        db = self._require_db()
        document = dict(movie)
        tmdb_id = int(document["tmdb_id"])
        existing = db.movies.find_one({"tmdb_id": tmdb_id}) or {}
        document.pop("_id", None)
        document["tmdb_id"] = tmdb_id
        document["id"] = existing.get("id", movie.get("id", TMDB_MOVIE_ID_OFFSET + tmdb_id))
        document["tmdb_rating"] = document.get("tmdb_rating", document.get("vote_average"))
        document["tmdb_vote_count"] = document.get("tmdb_vote_count", document.get("vote_count", 0))
        document["imdb_rating"] = None
        document["data_source"] = "tmdb"
        document["last_synced_at"] = datetime.now(timezone.utc)
        document["catalog_active"] = existing.get("catalog_active", True)
        overrides = existing.get("admin_overrides", {})
        document["admin_overrides"] = overrides
        document.update(overrides)
        db.movies.update_one({"tmdb_id": tmdb_id}, {"$set": document}, upsert=True)
        if fetch_after:
            return self.get_movie_by_tmdb_id(tmdb_id)
        return document

    def update_movie(self, tmdb_id, changes):
        allowed_fields = {"title", "tagline", "overview", "genres", "homepage"}
        updates = {key: value for key, value in changes.items() if key in allowed_fields}
        if not updates:
            return self.get_movie_by_tmdb_id(tmdb_id)
        for field in ("title", "tagline", "overview", "homepage"):
            if field in updates and not isinstance(updates[field], str):
                raise ValueError(f"{field} must be text.")
        if "genres" in updates and (
            not isinstance(updates["genres"], list)
            or any(not isinstance(value, str) for value in updates["genres"])
        ):
            raise ValueError("genres must be a list of strings.")
        now = datetime.now(timezone.utc)
        mongo_updates = {field: value for field, value in updates.items()}
        mongo_updates["updated_at"] = now
        mongo_updates.update({f"admin_overrides.{field}": value for field, value in updates.items()})
        self._require_db().movies.update_one(
            {"tmdb_id": int(tmdb_id)}, {"$set": mongo_updates}
        )
        return self.get_movie_by_tmdb_id(tmdb_id)

    def delete_movie(self, tmdb_id):
        result = self._require_db().movies.update_one(
            {"tmdb_id": int(tmdb_id), "catalog_active": {"$ne": False}},
            {"$set": {"catalog_active": False, "deleted_at": datetime.now(timezone.utc)}}
        )
        return result.modified_count == 1

# Singleton DataStore Instance
store = DataStore()
