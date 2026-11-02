"""
Flask Backend Application for CQ - Cinema Quotient
Intelligent Movie Recommendation System with Multi-User Authentication,
Google Sign-In, Instant Content Creation & Comprehensive Admin Portal.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
load_dotenv()

from email_validator import EmailNotValidError, validate_email
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash

from .data_store import DatabaseUnavailable, TMDB_MOVIE_ID_OFFSET, store
from email_service import EmailServiceError, send_otp_email
from recommendation_engine import engine
from search_service import search_engine
from tmdb_service import (
    TMDBError,
    TMDBService,
    get_backdrop_url,
    get_poster_url,
    normalize_movie_credits,
    normalize_movie_details,
    select_youtube_trailer
)

import os

frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))
app = Flask(__name__, template_folder=os.path.join(frontend_dir, 'templates'), static_folder=os.path.join(frontend_dir, 'static'))
logger = logging.getLogger(__name__)
secret_key = os.environ.get('SECRET_KEY')
if not secret_key or len(secret_key) < 32:
    if os.environ.get('APP_ENV') == 'production':
        raise RuntimeError('Set a strong SECRET_KEY before starting in production.')
    secret_key = secrets.token_hex(32)
if os.environ.get('APP_ENV') == 'production' and not os.environ.get('MONGODB_URI'):
    raise RuntimeError('Set MONGODB_URI before starting in production.')
app.secret_key = secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('APP_ENV') == 'production',
    PERMANENT_SESSION_LIFETIME=timedelta(days=14)
)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
)

oauth = OAuth(app)
oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

admin_email = os.environ.get('ADMIN_EMAIL', '').strip().lower()
admin_password_hash = os.environ.get('ADMIN_PASSWORD_HASH', '')
if admin_email and admin_password_hash and store.configured:
    try:
        check_password_hash(admin_password_hash, 'configuration-check')
    except (ValueError, TypeError) as error:
        raise RuntimeError('ADMIN_PASSWORD_HASH is not a valid Werkzeug password hash.') from error
    store.provision_admin(admin_email, admin_password_hash)
elif admin_email or admin_password_hash:
    logger.warning('Admin provisioning needs both ADMIN_EMAIL and ADMIN_PASSWORD_HASH.')

# Initialize engine catalog and search engine with dynamic store movies
try:
    initial_movies = store.get_all_movies()
    engine.refresh_catalog(initial_movies)
    search_engine.refresh_catalog(initial_movies)
except DatabaseUnavailable:
    logger.warning('MongoDB catalog unavailable; starting with an empty recommendation catalog.')
    engine.refresh_catalog([])
    search_engine.refresh_catalog([])
tmdb = TMDBService()
TMDB_GENRES = {
    12: 'Adventure', 14: 'Fantasy', 16: 'Animation', 18: 'Drama', 27: 'Horror',
    28: 'Action', 35: 'Comedy', 36: 'History', 37: 'Western', 53: 'Thriller',
    80: 'Crime', 99: 'Documentary', 878: 'Sci-Fi', 9648: 'Mystery', 10402: 'Music',
    10749: 'Romance', 10751: 'Family', 10752: 'War', 10770: 'TV Movie'
}

# ---------------------------------------------------------------------------
# Helper Context & Auth Utilities
# ---------------------------------------------------------------------------

def get_current_user():
    """Retrieves current logged-in user from session. Returns None if unauthenticated guest."""
    if 'user_id' in session:
        user = store.get_user_by_id(session['user_id'])
        if user:
            return user
    return None

@app.context_processor
def inject_user():
    """Injects active user profile into all Jinja2 HTML templates."""
    user = get_current_user()
    google_profile = session.get('google_onboarding') or {}
    return dict(current_user=user, google_onboarding={
        key: google_profile[key] for key in ('name', 'email', 'picture') if key in google_profile
    })

def require_admin():
    """Returns True if current user has admin role."""
    user = get_current_user()
    return user and user.get("role") == "admin"


def tmdb_movie_to_app_movie(data):
    tmdb_id = int(data.get('tmdb_id') or data.get('id'))
    release_date = data.get('release_date') or ''
    try:
        year = int(release_date[:4]) if release_date else None
    except ValueError:
        year = None

    if data.get('genres') and isinstance(data['genres'][0], dict):
        genres = [genre.get('name') for genre in data['genres'] if genre.get('name')]
    elif data.get('genres'):
        genres = data['genres']
    else:
        genres = [TMDB_GENRES[genre_id] for genre_id in data.get('genre_ids', []) if genre_id in TMDB_GENRES]

    runtime = data.get('runtime')
    duration = f"{runtime // 60}h {runtime % 60}m" if isinstance(runtime, int) and runtime > 0 else None
    poster_path = data.get('poster_path')
    backdrop_path = data.get('backdrop_path')
    vote_average = data.get('tmdb_rating', data.get('vote_average'))
    vote_count = data.get('tmdb_vote_count', data.get('vote_count', 0))
    directors = data.get('directors') or []
    if not directors and data.get('director'):
        directors = [data['director']]
    return {
        **data,
        'id': TMDB_MOVIE_ID_OFFSET + tmdb_id,
        'tmdb_id': tmdb_id,
        'title': data.get('title') or data.get('original_title') or 'Untitled',
        'year': year,
        'genres': genres,
        'director': data.get('director') or ', '.join(directors),
        'directors': directors,
        'cast': data.get('cast') or [],
        'overview': data.get('overview') or '',
        'keywords': data.get('keywords') or [],
        'tmdb_rating': vote_average,
        'tmdb_vote_count': vote_count,
        'vote_average': vote_average,
        'vote_count': vote_count,
        'imdb_rating': None,
        'poster_path': poster_path,
        'poster_url': data.get('poster_url') or get_poster_url(poster_path),
        'backdrop_path': backdrop_path,
        'backdrop_url': data.get('backdrop_url') or get_backdrop_url(backdrop_path),
        'duration': duration,
        'maturity': data.get('maturity'),
        'youtube_trailer_id': data.get('youtube_trailer_id'),
        'trailer': data.get('trailer'),
        'watch_providers': data.get('watch_providers') or {},
        'watch_provider_country': data.get('watch_provider_country', 'IN'),
        'data_source': 'tmdb',
        'last_synced_at': datetime.now(timezone.utc),
        'catalog_active': data.get('catalog_active', True)
    }


def public_user(user):
    if not user:
        return None
    clean_user = dict(user)
    for private_key in ('_id', 'password_hash', 'google_sub'):
        clean_user.pop(private_key, None)
    return clean_user


def normalize_email(value):
    if not isinstance(value, str):
        return None
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def otp_digest(email, otp):
    key = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return hmac.new(key, f'{email}:{otp}'.encode(), hashlib.sha256).hexdigest()


@app.before_request
def validate_mutation_origin():
    if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        return None
    origin = request.headers.get('Origin') or request.headers.get('Referer')
    if not origin:
        return jsonify({"error": "Request origin could not be verified."}), 403
    request_origin = urlparse(origin)
    allowed_origins = {
        (urlparse(request.host_url).scheme, urlparse(request.host_url).netloc),
        (urlparse(os.environ.get('APP_ORIGIN') or request.host_url).scheme,
         urlparse(os.environ.get('APP_ORIGIN') or request.host_url).netloc)
    }
    if (request_origin.scheme, request_origin.netloc) not in allowed_origins:
        return jsonify({"error": "Cross-site request rejected."}), 403
    return None


@app.errorhandler(DatabaseUnavailable)
def handle_database_unavailable(error):
    logger.error('Database unavailable: %s', error)
    return jsonify({"error": "Account services are temporarily unavailable. Please try again later."}), 503


@app.errorhandler(PyMongoError)
def handle_database_error(error):
    logger.exception('MongoDB operation failed.')
    return jsonify({"error": "Account services are temporarily unavailable. Please try again later."}), 503


@app.errorhandler(429)
def handle_rate_limit(error):
    return jsonify({"error": "Too many attempts. Please wait and try again."}), 429

# ---------------------------------------------------------------------------
# Page Routes (Frontend View Controllers)
# ---------------------------------------------------------------------------

@app.route('/')
def index_page():
    return redirect(url_for('home_page'))

@app.route('/home')
def home_page():
    return render_template('home.html')

@app.route('/search')
def search_page():
    query = request.args.get('q', '').strip()
    return render_template('search.html', initial_query=query)

@app.route('/movie/<int:movie_id>')
def movie_details_page(movie_id):
    if movie_id < TMDB_MOVIE_ID_OFFSET and not store.get_movie_by_id(movie_id):
        return redirect(url_for('home_page'))
    return render_template('details.html', movie_id=movie_id)

@app.route('/recommendations')
def recommendations_page():
    return render_template('recommendations.html')

@app.route('/watchlist')
def watchlist_page():
    return render_template('watchlist.html')

@app.route('/profile')
def profile_page():
    return render_template('profile.html')

@app.route('/admin')
def admin_page():
    user = get_current_user()
    if not user or user.get("role") != "admin":
        return redirect(url_for('home_page'))
    return render_template('admin.html')

# ---------------------------------------------------------------------------
# User Authentication API Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/auth/me', methods=['GET'])
def api_auth_me():
    user = get_current_user()
    return jsonify({"user": public_user(user)})

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit('10 per minute')
def api_auth_login():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    password = data.get('password')
    if not email or not isinstance(password, str) or not password:
        return jsonify({"error": "Enter a valid email and password."}), 400

    if email == admin_email:
        return jsonify({"error": "Use the administrator sign-in option."}), 403
    user = store.authenticate_user(email, password)
    if not user or user.get('role') == 'admin':
        return jsonify({"error": "Invalid email or password."}), 401

    session.clear()
    session['user_id'] = user['user_id']
    session.permanent = True
    return jsonify({"success": True, "user": public_user(user)})


@app.route('/api/admin/login', methods=['POST'])
@limiter.limit('5 per minute')
def api_admin_login():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    password = data.get('password')
    if not isinstance(password, str):
        password = ''
    configured_email = normalize_email(admin_email)
    if not configured_email or not admin_password_hash:
        return jsonify({"error": "Administrator sign-in is not configured."}), 503
    try:
        password_valid = bool(password) and check_password_hash(admin_password_hash, password)
    except (ValueError, TypeError):
        password_valid = False
    if email != configured_email or not password_valid:
        return jsonify({"error": "Invalid administrator credentials."}), 401

    user = store.get_user_by_email(configured_email)
    if not user or user.get('role') != 'admin':
        return jsonify({"error": "Administrator account is not provisioned."}), 503
    session.clear()
    session['user_id'] = user['user_id']
    session.permanent = True
    return jsonify({"success": True, "user": public_user(user)})

@app.route('/api/auth/register', methods=['POST'])
@limiter.limit('5 per minute')
def api_auth_register():
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip() if isinstance(data.get('name'), str) else ''
    email = normalize_email(data.get('email'))
    password = data.get('password', '')
    confirm_password = data.get('confirm_password', '')
    if not name or not email or not isinstance(password, str) or not password:
        return jsonify({"error": "Name, valid email, and password are required."}), 400
    if len(name) > 120:
        return jsonify({"error": "Name must be 120 characters or fewer."}), 400
    if not isinstance(confirm_password, str) or password != confirm_password:
        return jsonify({"error": "Passwords do not match."}), 400
    if len(password) < 8 or len(password) > 128:
        return jsonify({"error": "Password must be between 8 and 128 characters."}), 400
    if admin_email and email == admin_email:
        return jsonify({"error": "This email is reserved for administrator sign-in."}), 409

    google_profile = session.get('google_onboarding')
    google_sub = None
    picture = None
    if google_profile:
        if int(time.time()) - google_profile.get('verified_at', 0) > 900:
            session.pop('google_onboarding', None)
            return jsonify({"error": "Google verification expired. Continue with Google again."}), 400
        if email != google_profile.get('email') or name != google_profile.get('name'):
            return jsonify({"error": "Use the verified name and email from your Google account."}), 400
        google_sub = google_profile.get('sub')
        picture = google_profile.get('picture')

    otp = f'{secrets.randbelow(1_000_000):06d}'
    otp_hash = otp_digest(email, otp)
    err = store.issue_registration_otp(
        email, name, generate_password_hash(password), otp_hash, google_sub, picture
    )
    if err:
        status = 409 if 'already exists' in err else 429
        return jsonify({"error": err}), status
    try:
        send_otp_email(email, otp)
    except EmailServiceError:
        logger.exception('Verification email delivery failed for %s.', email)
        store.cancel_pending_registration(email, otp_hash)
        return jsonify({"error": "Could not send the verification email. Check your address or try again later."}), 503
    return jsonify({"success": True, "verification_required": True}), 202


@app.route('/api/auth/verify', methods=['POST'])
@limiter.limit('10 per minute')
def api_auth_verify():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    otp = str(data.get('otp', '')).strip()
    if not email or not re.fullmatch(r'\d{6}', otp):
        return jsonify({"error": "Enter a valid email and six-digit verification code."}), 400
    user, error = store.verify_registration_otp(email, otp_digest(email, otp))
    if error:
        status = 429 if 'Too many' in error else 400
        return jsonify({"error": error}), status
    session.clear()
    session['user_id'] = user['user_id']
    session.permanent = True
    return jsonify({"success": True, "user": public_user(user)})


@app.route('/api/auth/resend-otp', methods=['POST'])
@limiter.limit('3 per hour')
def api_auth_resend_otp():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    if not email:
        return jsonify({"error": "Enter a valid email address."}), 400
    otp = f'{secrets.randbelow(1_000_000):06d}'
    otp_hash = otp_digest(email, otp)
    _, error = store.resend_registration_otp(email, otp_hash)
    if error:
        status = 429 if 'wait' in error.lower() else 400
        return jsonify({"error": error}), status
    try:
        send_otp_email(email, otp)
    except EmailServiceError:
        logger.exception('Verification email resend failed for %s.', email)
        store.cancel_pending_registration(email, otp_hash)
        return jsonify({"error": "Could not send the verification email. Please start registration again later."}), 503
    return jsonify({"success": True}), 200

@app.route('/auth/google')
@limiter.limit('10 per minute')
def google_login():
    if not os.environ.get('GOOGLE_CLIENT_ID') or not os.environ.get('GOOGLE_CLIENT_SECRET'):
        return 'Google Sign-In is not configured on this server.', 503

    redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI') or url_for('google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        logger.exception('Google OAuth callback failed.')
        return redirect(url_for('home_page', auth_error='google'))

    google_user = token.get('userinfo') or {}
    email = normalize_email(google_user.get('email'))
    google_sub = google_user.get('sub')
    if not email or not google_sub or not google_user.get('email_verified'):
        return 'Google did not return a verified email address.', 400

    user = store.get_user_by_google_sub(google_sub)
    if user and user.get('role') == 'admin':
        return redirect(url_for('home_page', auth_error='google_admin'))
    if not user:
        user = store.get_user_by_email(email)
        if user:
            if user.get('role') == 'admin':
                return redirect(url_for('home_page', auth_error='google_admin'))
            user, error = store.link_google_account(
                email, google_sub, google_user.get('name'), google_user.get('picture')
            )
            if error:
                return redirect(url_for('home_page', auth_error='google_linked'))
    if user:
        session.clear()
        session['user_id'] = user['user_id']
        session.permanent = True
        return redirect(url_for('home_page'))

    session.clear()
    session['google_onboarding'] = {
        'sub': google_sub,
        'email': email,
        'name': (google_user.get('name') or email.split('@')[0])[:120],
        'picture': google_user.get('picture'),
        'verified_at': int(time.time())
    }
    session.permanent = True
    return redirect(url_for('home_page', google_signup='1'))

@app.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    session.clear()
    return jsonify({"success": True})

# ---------------------------------------------------------------------------
# Core Movie & Recommendation Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/genres', methods=['GET'])
def api_get_genres():
    genres = sorted({
        genre
        for movie in store.get_all_movies()
        for genre in movie.get('genres', [])
    })
    return jsonify({"genres": genres or sorted(set(TMDB_GENRES.values()))})

@app.route('/api/search', methods=['GET'])
def api_smart_search():
    query = request.args.get('q', '').strip()
    genre = request.args.get('genre', '').strip()
    year_value = request.args.get('year', '').strip()
    rating_value = request.args.get('min_rating', '').strip()
    sort_by = request.args.get('sort_by', 'relevance').strip().lower()
    try:
        page = int(request.args.get('page', '1'))
        limit = int(request.args.get('limit', '20'))
        if page < 1 or page > 500:
            raise ValueError
        if limit < 1 or limit > 100:
            limit = 20
        year = int(year_value) if year_value else None
        minimum_rating = float(rating_value) if rating_value else None
    except ValueError:
        return jsonify({"error": "Invalid page, limit, year, or minimum rating."}), 400

    results = search_engine.search(
        query=query,
        page=page,
        limit=limit,
        sort_by=sort_by,
        genre_filter=genre,
        year_filter=year,
        min_rating=minimum_rating,
        recommendation_engine=engine
    )

    user = get_current_user()
    user_id = user['user_id'] if user else None
    user_ratings = store.get_user_ratings(user_id) if user_id else {}
    for movie in results["movies"]:
        movie_id = movie["id"]
        movie["user_rating"] = user_ratings.get(movie_id)
        movie["in_watchlist"] = store.is_in_watchlist(user_id, movie_id) if user_id else False

    return jsonify(results)


@app.route('/api/search/suggestions', methods=['GET'])
def api_search_suggestions():
    query = request.args.get('q', '').strip()
    try:
        limit = int(request.args.get('limit', '6'))
        limit = max(1, min(12, limit))
    except ValueError:
        limit = 6

    suggestions_data = search_engine.get_suggestions(query=query, limit=limit)
    return jsonify(suggestions_data)


@app.route('/api/movies', methods=['GET'])
@app.route('/api/tmdb/movies', methods=['GET'])
def api_get_movies():
    query = request.args.get('q', '').strip()
    genre = request.args.get('genre', '').strip()
    year_value = request.args.get('year', '').strip()
    rating_value = request.args.get('min_rating', '').strip()
    sort_by = request.args.get('sort_by', 'popular')
    try:
        page = int(request.args.get('page', '1'))
        if page < 1 or page > 500:
            raise ValueError
        year = int(year_value) if year_value else None
        minimum_rating = float(rating_value) if rating_value else None
    except ValueError:
        return jsonify({"error": "Invalid page, year, or minimum rating."}), 400

    page_size = 20
    # Use smart search engine if query is provided
    if query:
        search_res = search_engine.search(
            query=query,
            page=page,
            limit=page_size,
            sort_by=sort_by if sort_by != 'popular' else 'relevance',
            genre_filter=genre,
            year_filter=year,
            min_rating=minimum_rating,
            recommendation_engine=engine
        )
        results = search_res["movies"]
        total_count = search_res["total_results"]
        total_pages = search_res["total_pages"]
    else:
        all_results = store.get_all_movies(
            search=None, genre=genre, year=year, min_rating=minimum_rating, sort_by=sort_by
        )
        start = (page - 1) * page_size
        results = all_results[start:start + page_size]
        total_count = len(all_results)
        total_pages = max(1, (total_count + page_size - 1) // page_size)

    user = get_current_user()
    user_id = user['user_id'] if user else None
    user_ratings = store.get_user_ratings(user_id) if user_id else {}
    for movie in results:
        movie_id = movie['id']
        movie['user_rating'] = user_ratings.get(movie_id)
        movie['in_watchlist'] = store.is_in_watchlist(user_id, movie_id) if user_id else False

    return jsonify({
        "movies": results,
        "count": len(results),
        "total_results": total_count,
        "page": page,
        "total_pages": total_pages,
        "source": "smart_search" if query else "mongodb"
    })

@app.route('/api/movies/<int:movie_id>', methods=['GET'])
@app.route('/api/movie/<int:movie_id>', methods=['GET'])
def api_get_movie_detail(movie_id):
    user = get_current_user()
    user_id = user['user_id'] if user else None
    movie = store.get_movie_by_id(movie_id)
    if not movie or movie.get('catalog_active') is False:
        return jsonify({"error": "Movie not found"}), 404

    movie = dict(movie)
    app_movie_id = movie['id']
    similar = engine.get_similar_movies(app_movie_id, top_n=6)
    watchlist = store.get_watchlist(user_id) if user_id else []
    user_ratings = store.get_user_ratings(user_id) if user_id else {}
    for item in similar:
        item['in_watchlist'] = item['id'] in watchlist
        item['user_rating'] = user_ratings.get(item['id'])

    movie['similar_movies'] = similar
    movie['user_rating'] = store.get_rating_for_movie(user_id, app_movie_id) if user_id else None
    movie['in_watchlist'] = store.is_in_watchlist(user_id, app_movie_id) if user_id else False
    movie['user_reviews'] = store.get_movie_ratings(app_movie_id)
    return jsonify(movie)


@app.route('/api/movies/<int:movie_id>/enrichment', methods=['GET'])
@app.route('/api/movie/<int:movie_id>/enrichment', methods=['GET'])
@limiter.limit('20 per minute')
def api_enrich_movie_details(movie_id):
    movie = store.get_movie_by_id(movie_id)
    if not movie or movie.get('catalog_active') is False:
        return jsonify({"error": "Movie not found"}), 404

    tmdb_id = int(movie['tmdb_id'])
    now = datetime.now(timezone.utc)
    try:
        details = normalize_movie_details(tmdb.get_movie_details(tmdb_id))
        credits = normalize_movie_credits(tmdb.get_movie_credits(tmdb_id))
        trailer = select_youtube_trailer(tmdb.get_movie_videos(tmdb_id))
    except TMDBError as error:
        logger.warning('TMDB detail enrichment failed (%s).', type(error).__name__)
        return jsonify({"error": "Supplementary movie details could not be loaded."}), 503

    enriched = dict(movie)
    enriched.update(tmdb_movie_to_app_movie(details))
    enriched.update({
        'directors': credits['directors'],
        'director': ', '.join(credits['directors']),
        'cast': credits['cast'],
        'crew': credits['crew'],
        'trailer': trailer,
        'youtube_trailer_id': trailer.get('key') if trailer else None,
        'details_synced_at': now
    })
    try:
        providers = tmdb.get_movie_watch_providers(tmdb_id, 'IN')
        enriched['watch_providers'] = providers
        enriched['watch_provider_country'] = 'IN'
        enriched['watch_provider_checked_at'] = now
    except TMDBError as error:
        logger.warning('TMDB watch-provider refresh failed (%s).', type(error).__name__)

    saved = store.cache_movie(enriched)
    return jsonify({
        "success": True,
        "movie": saved,
        "watch_provider_country": saved.get('watch_provider_country', 'IN'),
        "watch_provider_checked_at": saved.get('watch_provider_checked_at')
    })

@app.route('/api/recommendations', methods=['GET'])
def api_get_recommendations():
    alpha = float(request.args.get('alpha', 0.6))
    top_n = int(request.args.get('limit', 12))
    genre_filter = request.args.get('genre', '').strip()
    
    user = get_current_user()
    user_ratings = store.get_user_ratings(user['user_id']) if user else {}
    fav_genres = user.get("favorite_genres", ["Sci-Fi", "Action"]) if user else ["Sci-Fi", "Action", "Drama"]
    
    recs = engine.get_hybrid_recommendations(
        user_ratings=user_ratings,
        favorite_genres=fav_genres,
        alpha=alpha,
        top_n=20,
        all_ratings=store.get_rating_events(),
        active_user_id=user['user_id'] if user else None
    )
    
    watchlist = store.get_watchlist(user['user_id']) if user else []
    for r in recs:
        r['in_watchlist'] = r['id'] in watchlist
        
    if genre_filter and genre_filter != 'All':
        recs = [r for r in recs if genre_filter in r['genres']]
        
    return jsonify({
        "recommendations": recs[:top_n],
        "is_cold_start": len(user_ratings) < 2,
        "active_ratings_count": len(user_ratings),
        "favorite_genres": fav_genres
    })

@app.route('/api/rate', methods=['POST'])
def api_submit_rating():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Please sign in to submit a rating"}), 401
        
    data = request.get_json() or {}
    movie_id = data.get('movie_id')
    rating = data.get('rating')
    review = data.get('review', '')
    
    if not movie_id or rating is None:
        return jsonify({"error": "movie_id and rating are required"}), 400
        
    updated_ratings = store.add_rating(user['user_id'], movie_id, rating, review)
    return jsonify({
        "success": True,
        "movie_id": int(movie_id),
        "rating": float(rating),
        "total_ratings": len(updated_ratings)
    })

@app.route('/api/rate/<int:movie_id>', methods=['DELETE'])
def api_delete_rating(movie_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    updated_ratings = store.remove_rating(user['user_id'], movie_id)
    return jsonify({"success": True, "total_ratings": len(updated_ratings)})

@app.route('/api/watchlist', methods=['GET'])
def api_get_watchlist():
    user = get_current_user()
    if not user:
        return jsonify({"watchlist": [], "count": 0, "is_guest": True})
        
    user_id = user['user_id']
    w_ids = store.get_watchlist(user_id)
    user_ratings = store.get_user_ratings(user_id)
    
    watchlist_movies = []
    for m_id in w_ids:
        m = store.get_movie_by_id(m_id)
        if m:
            item = dict(m)
            item['in_watchlist'] = True
            item['user_rating'] = user_ratings.get(m_id, None)
            watchlist_movies.append(item)
            
    return jsonify({"watchlist": watchlist_movies, "count": len(watchlist_movies), "is_guest": False})

@app.route('/api/watchlist/add', methods=['POST'])
def api_add_watchlist():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Please sign in to use watchlist"}), 401
        
    data = request.get_json() or {}
    movie_id = data.get('movie_id')
    if not movie_id:
        return jsonify({"error": "movie_id is required"}), 400
        
    w_list = store.add_to_watchlist(user['user_id'], movie_id)
    return jsonify({"success": True, "watchlist": w_list})

@app.route('/api/watchlist/remove', methods=['POST'])
def api_remove_watchlist():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.get_json() or {}
    movie_id = data.get('movie_id')
    if not movie_id:
        return jsonify({"error": "movie_id is required"}), 400
        
    w_list = store.remove_from_watchlist(user['user_id'], movie_id)
    return jsonify({"success": True, "watchlist": w_list})

@app.route('/api/profile', methods=['GET'])
def api_get_profile():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized", "profile": None}), 401
        
    user_id = user['user_id']
    rating_history = store.get_user_rating_history(user_id)
    watchlist_ids = store.get_watchlist(user_id)
    
    clean_user = public_user(user)
    
    return jsonify({
        "profile": clean_user,
        "rating_history": rating_history,
        "ratings_count": len(rating_history),
        "watchlist_count": len(watchlist_ids)
    })

@app.route('/api/profile/genres', methods=['POST'])
def api_update_profile_genres():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
        
    data = request.get_json() or {}
    genres = data.get('genres', [])
    updated = store.update_favorite_genres(user['user_id'], genres)
    return jsonify({"success": True, "favorite_genres": updated})

# ---------------------------------------------------------------------------
# Admin Portal REST API Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/admin/users', methods=['GET'])
def api_admin_get_users():
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    return jsonify({"users": store.get_all_users(request.args.get('q', '').strip())})

@app.route('/api/admin/users/<int:user_id>', methods=['GET'])
def api_admin_get_user(user_id):
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    user = store.get_admin_user(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"user": user})

@app.route('/api/admin/users/<int:user_id>', methods=['PUT'])
def api_admin_update_user(user_id):
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    data = request.get_json(silent=True) or {}
    if any(field in data for field in ('role', 'email', 'password', 'password_hash', 'google_sub')):
        return jsonify({"error": "Only name and favorite genres can be edited here."}), 400
    try:
        user = store.update_user_profile(user_id, data)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"success": True, "user": user})

@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
def api_admin_delete_user(user_id):
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    
    current = get_current_user()
    if current and current['user_id'] == user_id:
        return jsonify({"error": "Cannot delete your own admin account while logged in"}), 400
        
    success = store.delete_user(user_id)
    return jsonify({"success": success})

@app.route('/api/admin/ratings', methods=['GET'])
def api_admin_get_ratings():
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    return jsonify({"ratings": store.get_all_ratings()})

@app.route('/api/admin/ratings', methods=['PUT'])
def api_admin_update_rating():
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    data = request.get_json() or {}
    user_id = data.get('user_id')
    movie_id = data.get('movie_id')
    rating = data.get('rating')
    review = data.get('review', '')
    
    if not user_id or not movie_id or rating is None:
        return jsonify({"error": "user_id, movie_id, rating are required"}), 400
        
    success = store.admin_update_rating(user_id, movie_id, rating, review)
    return jsonify({"success": success})

@app.route('/api/admin/ratings', methods=['DELETE'])
def api_admin_delete_rating():
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    data = request.get_json() or {}
    user_id = data.get('user_id')
    movie_id = data.get('movie_id')
    
    if not user_id or not movie_id:
        return jsonify({"error": "user_id and movie_id are required"}), 400
        
    success = store.admin_delete_rating(user_id, movie_id)
    return jsonify({"success": success})

@app.route('/api/admin/movies', methods=['GET'])
def api_admin_get_movies():
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    movies = store.get_all_movies(
        search=request.args.get('q', '').strip(),
        include_inactive=request.args.get('include_inactive') == 'true'
    )
    return jsonify({"movies": movies, "count": len(movies)})

@app.route('/api/admin/movies/<int:tmdb_id>', methods=['GET'])
def api_admin_get_movie(tmdb_id):
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    movie = store.get_movie_by_tmdb_id(tmdb_id)
    if not movie:
        return jsonify({"error": "Movie not found"}), 404
    return jsonify({"movie": movie})

@app.route('/api/admin/movies/sync', methods=['POST'])
@limiter.limit('6 per minute')
def api_admin_sync_movies():
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    data = request.get_json(silent=True) or {}
    query = data.get('query', '').strip() if isinstance(data.get('query', ''), str) else ''
    region = data.get('region', 'IN')
    try:
        page = int(data.get('page', 1))
        results = (
            tmdb.search_movies(query, page=page)
            if query else tmdb.discover_movies(page=page, region=region, sort_by='popularity.desc')
        )
        synced = []
        for result in results.get('results', [])[:10]:
            tmdb_id = int(result['id'])
            details = normalize_movie_details(tmdb.get_movie_details(tmdb_id))
            credits = normalize_movie_credits(tmdb.get_movie_credits(tmdb_id))
            trailer = select_youtube_trailer(tmdb.get_movie_videos(tmdb_id))
            try:
                providers = tmdb.get_movie_watch_providers(tmdb_id, region)
            except TMDBError:
                providers = {"country": region.upper() if isinstance(region, str) else "IN"}
            movie = tmdb_movie_to_app_movie(details)
            movie.update({
                'directors': credits['directors'],
                'director': ', '.join(credits['directors']),
                'cast': credits['cast'],
                'crew': credits['crew'],
                'trailer': trailer,
                'youtube_trailer_id': trailer.get('key') if trailer else None,
                'watch_providers': providers,
                'watch_provider_country': providers.get('country', 'IN')
            })
            synced.append(store.cache_movie(movie))
    except (ValueError, KeyError) as error:
        return jsonify({"error": f"Invalid TMDB sync request: {error}"}), 400
    except TMDBError as error:
        logger.warning('TMDB admin sync failed (%s).', type(error).__name__)
        return jsonify({"error": "TMDB sync is temporarily unavailable."}), 503
    catalog_movies = store.get_all_movies()
    engine.refresh_catalog(catalog_movies)
    search_engine.refresh_catalog(catalog_movies)
    return jsonify({"success": True, "synced": len(synced), "movies": synced})

@app.route('/api/admin/movies/<int:movie_id>', methods=['PUT'])
def api_admin_update_movie(movie_id):
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    data = request.get_json(silent=True) or {}
    if any(field in data for field in ('tmdb_id', 'id', 'imdb_rating', 'tmdb_rating', 'vote_average', 'data_source')):
        return jsonify({"error": "TMDB identifiers and ratings are managed by the catalog sync."}), 400
    try:
        updated_movie = store.update_movie(movie_id, data)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not updated_movie:
        return jsonify({"error": "Movie not found"}), 404
    catalog_movies = store.get_all_movies()
    engine.refresh_catalog(catalog_movies)
    search_engine.refresh_catalog(catalog_movies)
    return jsonify({"success": True, "movie": updated_movie})

@app.route('/api/admin/movies/<int:movie_id>', methods=['DELETE'])
def api_admin_delete_movie(movie_id):
    if not require_admin():
        return jsonify({"error": "Admin access required"}), 403
    success = store.delete_movie(movie_id)
    if success:
        catalog_movies = store.get_all_movies()
        engine.refresh_catalog(catalog_movies)
        search_engine.refresh_catalog(catalog_movies)
    return jsonify({"success": success})

# ---------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', '5000')),
        debug=os.environ.get('APP_ENV') == 'development',
        use_reloader=False
    )
