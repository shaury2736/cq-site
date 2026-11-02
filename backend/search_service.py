"""
Smart Search Engine for CQ - Cinema Quotient
Provides:
- Intelligent search with typo tolerance (RapidFuzz, Levenshtein, Phonetic & Token Matching)
- Multi-field search (title, alternative titles, cast, directors, genres, keywords, overview, year)
- Natural language intent extraction ("movies with Leonardo DiCaprio", "Nolan movies", "movies similar to X")
- Recommendation engine integration for similarity queries
- Strict relevance ranking based on specification priority
- Real-time suggestions & autocomplete with character highlighting
- Search result caching & performance optimization
"""

import html
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from rapidfuzz import fuzz, process
    from rapidfuzz.distance import Levenshtein
except ImportError:
    fuzz = None
    process = None
    Levenshtein = None


def normalize_text(text: str) -> str:
    """Normalize text for consistent searching: lowercased, punctuation stripped, normalized spaces."""
    if not text:
        return ""
    text = text.lower()
    # Replace hyphens and underscores with spaces so "spider-man" matches "spider man"
    text = re.sub(r'[-_]+', ' ', text)
    # Remove remaining punctuation except alphanumeric and spaces
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def normalize_phonetic(text: str) -> str:
    """
    Phonetic normalization for typo resilience:
    - Normalizes common phonetic representations ('nite' -> 'night')
    - Collapses repeated consecutive characters ('baahubali' -> 'bahubali', 'interstelar' -> 'interstellar')
    - Strips spaces and hyphens for compact comparison ('spider-man' -> 'spiderman')
    """
    if not text:
        return ""
    s = text.lower()
    s = re.sub(r'[-_\s]+', '', s)
    s = re.sub(r'nite\b', 'night', s)
    s = re.sub(r'ph', 'f', s)
    # Collapse double or multiple consecutive characters
    s = re.sub(r'(.)\1+', r'\1', s)
    return s


def extract_franchise_root(title: str) -> str:
    """
    Extracts the root franchise or main title before subtitles, colons, or part numbers.
    e.g. 'Avengers: Endgame' -> 'Avengers'
         'The Dark Knight Rises' -> 'The Dark Knight'
         'Spider-Man: Across the Spider-Verse' -> 'Spider-Man'
         'Harry Potter and the Goblet of Fire' -> 'Harry Potter'
         'Baahubali: The Beginning' -> 'Baahubali'
    """
    if not title:
        return ""
    # Split by colon, dash, or em-dash
    parts = re.split(r'[:\-–—]', title)
    root = parts[0].strip()

    # Check for known franchise patterns
    for prefix in ["Harry Potter", "The Dark Knight", "The Godfather", "Star Wars", "Lord of the Rings"]:
        if title.lower().startswith(prefix.lower()):
            return prefix

    return root


def highlight_matches(text: str, query: str) -> str:
    """
    Safely highlights matching query characters/words in text using <mark class="search-highlight">.
    Escapes HTML to prevent XSS.
    """
    if not text:
        return ""
    escaped_text = html.escape(text)
    if not query:
        return escaped_text

    norm_query = normalize_text(query)
    words = [re.escape(w) for w in norm_query.split() if len(w) > 0]
    if not words:
        return escaped_text

    # Sort longest words first so larger matches highlight first
    words.sort(key=len, reverse=True)
    pattern = re.compile(f"({'|'.join(words)})", re.IGNORECASE)
    return pattern.sub(r'<mark class="search-highlight">\1</mark>', escaped_text)


class SmartSearchEngine:
    def __init__(self, movies: Optional[List[Dict[str, Any]]] = None):
        self.movies: List[Dict[str, Any]] = []
        self.movie_map: Dict[int, Dict[str, Any]] = {}
        self.titles_list: List[str] = []
        self.canonical_targets: Dict[str, str] = {}  # normalized key -> canonical display string
        self.movie_by_title: Dict[str, List[int]] = {}
        self.directors_set: Set[str] = set()
        self.actors_set: Set[str] = set()
        self.genres_set: Set[str] = set()
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._cache_ttl = 300  # 5 minutes cache

        if movies:
            self.refresh_catalog(movies)

    def refresh_catalog(self, movies: List[Dict[str, Any]]):
        """Indexes all active movies for rapid in-memory fuzzy and multi-field search."""
        self.movies = []
        self.movie_map = {}
        self.titles_list = []
        self.canonical_targets = {}
        self.movie_by_title = {}
        self.directors_set = set()
        self.actors_set = set()
        self.genres_set = set()
        self._cache.clear()

        for source in movies:
            if not isinstance(source, dict):
                continue
            if source.get("catalog_active") is False:
                continue

            movie = dict(source)
            movie_id = movie.get("id") or movie.get("tmdb_id")
            if movie_id is None:
                continue
            movie_id = int(movie_id)
            movie["id"] = movie_id

            # Canonical title normalization
            title = str(movie.get("title") or movie.get("original_title") or "Untitled")
            movie["title"] = title
            norm_title = normalize_text(title)
            movie["_norm_title"] = norm_title
            movie["_phon_title"] = normalize_phonetic(title)
            movie["_title_words"] = [w for w in norm_title.split() if w]

            # Root franchise
            root_title = extract_franchise_root(title)
            movie["_root_title"] = root_title
            movie["_norm_root"] = normalize_text(root_title)
            movie["_phon_root"] = normalize_phonetic(root_title)

            # Store in target spell dictionary
            if norm_title:
                self.canonical_targets[norm_title] = title
            if movie["_norm_root"] and movie["_norm_root"] not in self.canonical_targets:
                self.canonical_targets[movie["_norm_root"]] = root_title

            original_title = str(movie.get("original_title") or "")
            movie["_norm_original_title"] = normalize_text(original_title)
            movie["_phon_orig"] = normalize_phonetic(original_title)

            # Year extraction
            year = movie.get("year")
            if not year and movie.get("release_date"):
                try:
                    year = int(str(movie["release_date"])[:4])
                except (ValueError, TypeError):
                    year = None
            movie["year"] = year

            # Genres extraction
            raw_genres = movie.get("genres") or []
            genres = [
                g.get("name", "") if isinstance(g, dict) else str(g)
                for g in raw_genres
            ]
            movie["genres"] = [g.strip() for g in genres if g and g.strip()]
            movie["_norm_genres"] = [normalize_text(g) for g in movie["genres"]]
            for g in movie["genres"]:
                self.genres_set.add(g)

            # Directors extraction
            directors = movie.get("directors") or []
            if not directors and movie.get("director"):
                directors = [movie["director"]]
            norm_directors = []
            clean_directors = []
            for d in directors:
                name = d.get("name", "") if isinstance(d, dict) else str(d)
                if name.strip():
                    clean_directors.append(name.strip())
                    norm_directors.append(normalize_text(name))
                    self.directors_set.add(name.strip())
            movie["directors"] = clean_directors
            movie["director"] = ", ".join(clean_directors)
            movie["_norm_directors"] = norm_directors

            # Cast extraction (keep top 15 actors)
            raw_cast = movie.get("cast") or []
            cast_names = []
            norm_cast = []
            for person in raw_cast[:15]:
                name = person.get("name", "") if isinstance(person, dict) else str(person)
                if name.strip():
                    cast_names.append(name.strip())
                    norm_cast.append(normalize_text(name))
                    self.actors_set.add(name.strip())
            movie["cast_names"] = cast_names
            movie["_norm_cast"] = norm_cast

            # Overview & Keywords
            overview = str(movie.get("overview") or "")
            movie["overview"] = overview
            movie["_norm_overview"] = normalize_text(overview)

            raw_keywords = movie.get("keywords") or []
            keywords = [
                k.get("name", "") if isinstance(k, dict) else str(k)
                for k in raw_keywords
            ]
            movie["keywords"] = [k.strip() for k in keywords if k and k.strip()]
            movie["_norm_keywords"] = [normalize_text(k) for k in movie["keywords"]]

            # Ratings & Popularity
            rating = movie.get("tmdb_rating", movie.get("vote_average", 0.0))
            try:
                movie["vote_average"] = float(rating) if rating is not None else 0.0
            except (ValueError, TypeError):
                movie["vote_average"] = 0.0

            popularity = movie.get("popularity", 0.0)
            try:
                movie["popularity"] = float(popularity) if popularity is not None else 0.0
            except (ValueError, TypeError):
                movie["popularity"] = 0.0

            self.movies.append(movie)
            self.movie_map[movie_id] = movie

            if norm_title:
                self.titles_list.append(title)
                self.movie_by_title.setdefault(norm_title, []).append(movie_id)

    # ---------------------------------------------------------------------------
    # Intent Detection
    # ---------------------------------------------------------------------------

    def parse_query_intent(self, query: str) -> Dict[str, Any]:
        """
        Extracts semantic search intent such as:
        - "movies with Leonardo DiCaprio" -> type='actor', target='Leonardo DiCaprio'
        - "Christopher Nolan movies" -> type='director', target='Christopher Nolan'
        - "romantic movies released in 2020" -> type='filtered', year=2020, genre='Romance'
        - "movies similar to Interstellar" -> type='similar', target='Interstellar'
        - "sci-fi movies" -> type='genre', target='Sci-Fi'
        """
        raw_query = query.strip()
        norm = normalize_text(raw_query)

        intent = {
            "raw_query": raw_query,
            "clean_query": norm,
            "type": "general",
            "actor": None,
            "director": None,
            "genre": None,
            "year": None,
            "similar_target": None,
            "keywords": []
        }

        # 1. Check for release year (1900 - 2099)
        year_match = re.search(r'\b(19\d\d|20\d\d)\b', norm)
        if year_match:
            intent["year"] = int(year_match.group(1))
            norm = re.sub(r'\b(19\d\d|20\d\d)\b', '', norm).strip()

        # 2. Check for "similar to <movie>" or "like <movie>"
        sim_match = re.search(r'(?:movies?\s+)?(?:similar\s+to|like)\s+(.+)', norm, re.IGNORECASE)
        if sim_match:
            intent["type"] = "similar"
            intent["similar_target"] = sim_match.group(1).strip()
            return intent

        # 3. Check for "movies with <actor>" or "starring <actor>"
        actor_match = re.search(r'(?:movies?\s+)?(?:with|starring|featuring)\s+(.+)', norm, re.IGNORECASE)
        if actor_match:
            candidate = actor_match.group(1).strip()
            matched_actor = self._find_matching_person(candidate, self.actors_set)
            if matched_actor:
                intent["type"] = "actor"
                intent["actor"] = matched_actor
                return intent

        # 4. Check for "movies directed by <director>" or "movies by <director>"
        dir_match = re.search(r'(?:movies?\s+)?(?:directed\s+by|by)\s+(.+)', norm, re.IGNORECASE)
        if dir_match:
            candidate = dir_match.group(1).strip()
            matched_director = self._find_matching_person(candidate, self.directors_set)
            if matched_director:
                intent["type"] = "director"
                intent["director"] = matched_director
                return intent

        # 5. Check for "<person> movies" (e.g. "Christopher Nolan movies" or "Nolan movies")
        person_match = re.search(r'(.+?)\s+movies?$', norm, re.IGNORECASE)
        if person_match:
            candidate = person_match.group(1).strip()
            matched_genre = self._find_matching_genre(candidate)
            if matched_genre:
                intent["type"] = "genre"
                intent["genre"] = matched_genre
                return intent

            matched_director = self._find_matching_person(candidate, self.directors_set)
            if matched_director:
                intent["type"] = "director"
                intent["director"] = matched_director
                return intent

            matched_actor = self._find_matching_person(candidate, self.actors_set)
            if matched_actor:
                intent["type"] = "actor"
                intent["actor"] = matched_actor
                return intent

        # 6. Check if query is a genre
        matched_genre = self._find_matching_genre(norm)
        if matched_genre:
            intent["type"] = "genre"
            intent["genre"] = matched_genre
            return intent

        # 7. Check if query is just a recognized director or actor
        matched_director = self._find_matching_person(norm, self.directors_set)
        if matched_director and not self.movie_by_title.get(norm):
            intent["type"] = "director"
            intent["director"] = matched_director
            return intent

        matched_actor = self._find_matching_person(norm, self.actors_set)
        if matched_actor and not self.movie_by_title.get(norm):
            intent["type"] = "actor"
            intent["actor"] = matched_actor
            return intent

        return intent

    def _find_matching_genre(self, text: str) -> Optional[str]:
        """Finds closest genre matching text."""
        norm = normalize_text(text)
        genre_synonyms = {
            "scifi": "Science Fiction",
            "sci fi": "Science Fiction",
            "science fiction": "Science Fiction",
            "action": "Action",
            "adventure": "Adventure",
            "animation": "Animation",
            "animated": "Animation",
            "anime": "Animation",
            "comedy": "Comedy",
            "crime": "Crime",
            "documentary": "Documentary",
            "drama": "Drama",
            "family": "Family",
            "kids": "Family",
            "fantasy": "Fantasy",
            "history": "History",
            "historical": "History",
            "horror": "Horror",
            "scary": "Horror",
            "music": "Music",
            "musical": "Music",
            "mystery": "Mystery",
            "romance": "Romance",
            "romantic": "Romance",
            "thriller": "Thriller",
            "war": "War",
            "western": "Western"
        }
        for syn, canonical in genre_synonyms.items():
            if norm == syn or syn in norm:
                return canonical
        for g in self.genres_set:
            if normalize_text(g) == norm:
                return g
        return None

    def _find_matching_person(self, text: str, person_set: Set[str]) -> Optional[str]:
        """Checks if text matches or partially matches a person in person_set."""
        norm = normalize_text(text)
        if not norm or len(norm) < 3:
            return None

        for person in person_set:
            if normalize_text(person) == norm:
                return person

        # Last name match (e.g. "Nolan" -> "Christopher Nolan", "DiCaprio" -> "Leonardo DiCaprio")
        parts = norm.split()
        for person in person_set:
            p_parts = normalize_text(person).split()
            if len(parts) == 1 and len(p_parts) > 1:
                if parts[0] == p_parts[-1]:
                    return person
            elif len(parts) > 1 and parts == p_parts:
                return person

        if fuzz and len(norm) >= 4:
            candidates = list(person_set)
            best = process.extractOne(norm, [normalize_text(p) for p in candidates], scorer=fuzz.ratio)
            if best and best[1] >= 85:
                idx = best[2]
                return candidates[idx]

        return None

    # ---------------------------------------------------------------------------
    # Spelling Correction & Typo Tolerance
    # ---------------------------------------------------------------------------

    def check_spelling_correction(self, query: str, intent: Optional[Dict[str, Any]] = None) -> Tuple[Optional[str], bool]:
        """
        Determines if user query contains spelling errors, returning:
        (corrected_query_string, was_corrected_bool).
        Examples:
        - "Interstelar" -> ("Interstellar", True)
        - "Avngers" -> ("Avengers", True)
        - "Incepton" -> ("Inception", True)
        - "Dark Nite" -> ("The Dark Knight", True)
        - "Hary Potter" -> ("Harry Potter", True)
        - "Bahubali" -> ("Baahubali", True)
        """
        raw_query = query.strip()
        norm_query = normalize_text(raw_query)
        phon_query = normalize_phonetic(raw_query)

        if not norm_query or len(norm_query) < 3:
            return query, False

        # If semantic intent (actor/director/genre/year) is found, do NOT spell-correct whole string to movie title
        if intent and intent.get("type") in {"actor", "director", "genre"}:
            return query, False

        # If exact match exists for title, no typo correction needed
        if norm_query in self.movie_by_title:
            return self.movie_map[self.movie_by_title[norm_query][0]]["title"], False

        # If exact match is a prefix of any movie (user is still actively typing), don't trigger typo banner
        for title_norm in self.movie_by_title.keys():
            if title_norm.startswith(norm_query):
                return query, False

        # 1. Phonetic exact match against root titles or full titles
        for movie in self.movies:
            if phon_query == movie["_phon_root"]:
                return movie["_root_title"], True
            if phon_query == movie["_phon_title"]:
                return movie["title"], True

        # 2. Token / word-level comparison and RapidFuzz scoring
        if fuzz and self.canonical_targets:
            candidate_keys = list(self.canonical_targets.keys())

            # For short single-word typing (e.g. 'Avng'), check if it matches a prefix of a word starting with same initial
            if len(norm_query.split()) == 1 and len(norm_query) >= 3:
                first_char = norm_query[0]
                prefix_candidates = []
                for movie in self.movies:
                    for w in movie["_title_words"]:
                        if w.startswith(first_char) and len(w) >= len(norm_query):
                            w_prefix = w[:len(norm_query)]
                            dist = Levenshtein.distance(norm_query, w_prefix) if Levenshtein else 99
                            if dist <= 1 or fuzz.ratio(norm_query, w_prefix) >= 75.0:
                                prefix_candidates.append((movie["_root_title"], fuzz.ratio(norm_query, w_prefix)))
                if prefix_candidates:
                    prefix_candidates.sort(key=lambda x: x[1], reverse=True)
                    top_root = prefix_candidates[0][0]
                    if normalize_text(top_root) != norm_query:
                        return top_root, True

            # A. First check against root titles and full titles using phonetic ratio & token_set_ratio
            best_root = None
            best_score = 0.0

            for key in candidate_keys:
                canonical = self.canonical_targets[key]
                p_key = normalize_phonetic(key)

                # Direct token set ratio on phonetic form
                score_phon = fuzz.token_set_ratio(phon_query, p_key)
                score_raw = fuzz.token_set_ratio(norm_query, key)
                score_ratio = fuzz.ratio(norm_query, key)
                score = max(score_phon, score_raw, score_ratio)

                if score > best_score:
                    best_score = score
                    best_root = canonical

            if best_root and best_score >= 82.0:
                if normalize_text(best_root) != norm_query:
                    return best_root, True

            # B. Single-word query matched against title words (e.g. "Avngers" -> "Avengers")
            if len(norm_query.split()) == 1:
                word_matches = []
                for movie in self.movies:
                    for w in movie["_title_words"]:
                        if len(w) >= 4:
                            r = fuzz.ratio(norm_query, w)
                            if r >= 80.0:
                                word_matches.append((movie["_root_title"], r))
                if word_matches:
                    word_matches.sort(key=lambda x: x[1], reverse=True)
                    top_root = word_matches[0][0]
                    if normalize_text(top_root) != norm_query:
                        return top_root, True

        return query, False

    # ---------------------------------------------------------------------------
    # Core Multi-Field Scoring Algorithm
    # ---------------------------------------------------------------------------

    def _score_movie(
        self,
        movie: Dict[str, Any],
        raw_query: str,
        norm_query: str,
        phon_query: str,
        query_words: List[str],
        intent: Dict[str, Any]
    ) -> float:
        """
        Calculates relevance score according to the 7-tier specification order:
        1. Exact title matches (1200+)
        2. Partial title matches (700-900)
        3. Highly similar spellings / fuzzy title matches (500-750)
        4. Alternative title matches (400-500)
        5. Actor/director matches (300-400)
        6. Genre and keyword matches (200-300)
        7. Description-based matches (50-200)
        + Popularity/Rating tie-breaker (+0 to 30)
        """
        score = 0.0
        norm_title = movie["_norm_title"]
        phon_title = movie["_phon_title"]
        norm_orig = movie["_norm_original_title"]
        phon_orig = movie["_phon_orig"]
        norm_root = movie["_norm_root"]
        phon_root = movie["_phon_root"]

        # Quality tie-breaker based on popularity and TMDB rating (capped at 30 points)
        pop_bonus = min(15.0, (movie.get("popularity", 0.0) / 40.0))
        rating_bonus = min(15.0, (movie.get("vote_average", 0.0) * 1.5))
        quality_score = pop_bonus + rating_bonus

        # Handle specific intents
        if intent["actor"]:
            target_actor = normalize_text(intent["actor"])
            for idx, actor in enumerate(movie["_norm_cast"]):
                if target_actor in actor:
                    actor_rank = max(1, 15 - idx)
                    return 950.0 + actor_rank * 5 + quality_score

        if intent["director"]:
            target_dir = normalize_text(intent["director"])
            for d in movie["_norm_directors"]:
                if target_dir in d:
                    return 950.0 + quality_score

        if intent["genre"]:
            target_genre = normalize_text(intent["genre"])
            if any(target_genre == g for g in movie["_norm_genres"]):
                genre_score = 700.0 + quality_score
                if intent.get("year") and movie.get("year") == intent["year"]:
                    genre_score += 250.0
                return genre_score

        # Year filter bonus if year present in query
        if intent.get("year") and movie.get("year") == intent["year"]:
            score += 150.0

        # Tier 1: Exact Title Match
        if norm_query == norm_title or norm_query == norm_root:
            return 1200.0 + quality_score

        # Phonetic exact match (e.g. 'interstelar' -> 'interstellar', 'spiderman' -> 'spider man', 'bahubali' -> 'baahubali')
        if phon_query == phon_root or phon_query == phon_title:
            return 1150.0 + quality_score

        # Tier 2: Partial & Prefix Title Matches
        # A. Perfect prefix match on whole title
        if norm_title.startswith(norm_query):
            length_diff = len(norm_title) - len(norm_query)
            return 900.0 - min(100.0, length_diff * 1.5) + quality_score

        # B. Prefix match on any word in the title (e.g. 'Avng' or 'Endg' in 'Avengers: Endgame')
        for w in movie["_title_words"]:
            if w.startswith(norm_query):
                length_diff = len(w) - len(norm_query)
                return 850.0 - min(80.0, length_diff * 2) + quality_score
            # Prefix typo match on words starting with same initial
            elif len(norm_query) >= 3 and len(w) >= len(norm_query) and w[0] == norm_query[0]:
                prefix_dist = Levenshtein.distance(norm_query, w[:len(norm_query)]) if Levenshtein else 99
                if prefix_dist <= 1:
                    return 820.0 + quality_score

        # C. Query words all present in title
        if len(query_words) > 1 and all(w in norm_title for w in query_words):
            return 800.0 + quality_score

        # D. Substring match
        if norm_query in norm_title:
            return 750.0 + quality_score

        # Tier 3: Fuzzy Title Matches (Typo Tolerance)
        if fuzz and len(norm_query) >= 3:
            # Score against root franchise and full title
            score_root_raw = fuzz.ratio(norm_query, norm_root)
            score_root_phon = fuzz.ratio(phon_query, phon_root)
            score_title_ts = fuzz.token_set_ratio(norm_query, norm_title)
            score_title_phon = fuzz.token_set_ratio(phon_query, phon_title)

            # Also score against individual words in title
            word_fuzz = 0.0
            for w in movie["_title_words"]:
                if len(w) >= 3:
                    w_ratio = fuzz.ratio(norm_query, w)
                    # Prefix typo check: compare query to equal-length prefix of word
                    if len(w) >= len(norm_query):
                        prefix_ratio = fuzz.ratio(norm_query, w[:len(norm_query)])
                        w_ratio = max(w_ratio, prefix_ratio * 0.95)
                    word_fuzz = max(word_fuzz, w_ratio)

            fuzzy_best = max(score_root_raw, score_root_phon, score_title_ts, score_title_phon, word_fuzz)

            if fuzzy_best >= 70.0:
                fuzzy_score = 500.0 + (fuzzy_best - 70.0) * (250.0 / 30.0)
                score = max(score, fuzzy_score + quality_score)

        # Tier 4: Alternative Title Match (original_title)
        if norm_orig:
            if norm_query == norm_orig or phon_query == phon_orig:
                score = max(score, 550.0 + quality_score)
            elif norm_orig.startswith(norm_query):
                score = max(score, 480.0 + quality_score)
            elif norm_query in norm_orig:
                score = max(score, 420.0 + quality_score)

        # Tier 5: Actor & Director Matches
        for d in movie["_norm_directors"]:
            if norm_query in d or any(w in d for w in query_words if len(w) > 2):
                score = max(score, 380.0 + quality_score)
                break

        for idx, a in enumerate(movie["_norm_cast"][:10]):
            if norm_query in a or any(w in a for w in query_words if len(w) > 2):
                cast_weight = max(0, 10 - idx) * 4
                score = max(score, 320.0 + cast_weight + quality_score)
                break

        # Tier 6: Genre and Keyword Matches
        for g in movie["_norm_genres"]:
            if norm_query == g or g.startswith(norm_query):
                score = max(score, 280.0 + quality_score)
                break

        for k in movie["_norm_keywords"]:
            if norm_query in k:
                score = max(score, 240.0 + quality_score)
                break

        # Tier 7: Description/Overview-based matches
        norm_overview = movie["_norm_overview"]
        if norm_overview and len(norm_query) >= 3:
            if norm_query in norm_overview:
                score = max(score, 180.0 + quality_score)
            else:
                matched_words = [w for w in query_words if len(w) > 2 and w in norm_overview]
                if matched_words:
                    word_ratio = len(matched_words) / len(query_words)
                    if word_ratio >= 0.5:
                        score = max(score, 80.0 + (word_ratio * 70.0) + quality_score)

        return score

    # ---------------------------------------------------------------------------
    # Public Search Method
    # ---------------------------------------------------------------------------

    def search(
        self,
        query: str,
        page: int = 1,
        limit: int = 20,
        sort_by: str = "relevance",
        genre_filter: Optional[str] = None,
        year_filter: Optional[int] = None,
        min_rating: Optional[float] = None,
        recommendation_engine: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Executes a smart search query with typo tolerance, ranking, and filtering.
        """
        raw_query = (query or "").strip()
        norm_query = normalize_text(raw_query)
        phon_query = normalize_phonetic(raw_query)

        # Cache lookup
        cache_key = f"{norm_query}:{page}:{limit}:{sort_by}:{genre_filter}:{year_filter}:{min_rating}"
        now = time.time()
        if cache_key in self._cache:
            timestamp, cached_result = self._cache[cache_key]
            if now - timestamp < self._cache_ttl:
                return cached_result

        # Edge case: Empty query
        if not norm_query:
            sorted_movies = sorted(self.movies, key=lambda m: m.get("popularity", 0.0), reverse=True)
            return self._paginate_results(
                sorted_movies, page, limit, query, corrected_query=query, spell_corrected=False, intent={"type": "empty"}
            )

        # Detect intent
        intent = self.parse_query_intent(raw_query)

        # Check for "movies similar to <X>"
        if intent["type"] == "similar" and intent.get("similar_target") and recommendation_engine:
            target_title = intent["similar_target"]
            # Find closest movie to target
            target_res = self.search(target_title, page=1, limit=1)
            if target_res["movies"]:
                target_movie = target_res["movies"][0]
                similar_movies = recommendation_engine.get_similar_movies(target_movie["id"], top_n=limit)
                return self._paginate_results(
                    similar_movies, page, limit, query, corrected_query=f"Similar to {target_movie['title']}",
                    spell_corrected=False, intent=intent
                )

        # Detect spelling correction
        corrected_title, was_corrected = self.check_spelling_correction(raw_query, intent)
        effective_query = corrected_title if was_corrected else raw_query
        effective_norm = normalize_text(effective_query)
        effective_phon = normalize_phonetic(effective_query)
        effective_words = effective_norm.split()

        # Score movies
        scored_movies: List[Tuple[Dict[str, Any], float]] = []

        for movie in self.movies:
            # Hard filters
            if genre_filter and genre_filter.casefold() != "all":
                if not any(g.casefold() == genre_filter.casefold() for g in movie["genres"]):
                    continue

            if year_filter is not None:
                if movie.get("year") != year_filter:
                    continue

            if min_rating is not None:
                if movie.get("vote_average", 0.0) < min_rating:
                    continue

            # Calculate score using effective (corrected) query
            score = self._score_movie(movie, raw_query, effective_norm, effective_phon, effective_words, intent)

            # If was corrected, also check if original had any score
            if was_corrected:
                orig_score = self._score_movie(movie, raw_query, norm_query, phon_query, norm_query.split(), intent)
                score = max(score, orig_score)

            if score > 0:
                scored_movies.append((movie, score))

        # Sort results
        if sort_by == "rating":
            scored_movies.sort(key=lambda item: (item[0].get("vote_average", 0.0), item[1]), reverse=True)
        elif sort_by == "year":
            scored_movies.sort(key=lambda item: (item[0].get("year") or 0, item[1]), reverse=True)
        elif sort_by == "popular":
            scored_movies.sort(key=lambda item: (item[0].get("popularity", 0.0), item[1]), reverse=True)
        else:  # "relevance"
            scored_movies.sort(key=lambda item: item[1], reverse=True)

        ranked_movies = [item[0] for item in scored_movies]

        result = self._paginate_results(
            ranked_movies,
            page=page,
            limit=limit,
            query=raw_query,
            corrected_query=corrected_title,
            spell_corrected=was_corrected,
            intent=intent
        )

        self._cache[cache_key] = (now, result)
        return result

    def _paginate_results(
        self,
        movies: List[Dict[str, Any]],
        page: int,
        limit: int,
        query: str,
        corrected_query: str,
        spell_corrected: bool,
        intent: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Paginates list of movies and packages API response format."""
        total = len(movies)
        start = (page - 1) * limit
        end = start + limit
        page_items = movies[start:end]

        clean_items = []
        for m in page_items:
            item = dict(m)
            for k in list(item.keys()):
                if k.startswith("_"):
                    item.pop(k, None)
            clean_items.append(item)

        total_pages = max(1, (total + limit - 1) // limit)

        return {
            "query": query,
            "corrected_query": corrected_query,
            "spell_corrected": spell_corrected,
            "total_results": total,
            "count": total,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "movies": clean_items,
            "intent": {
                "type": intent.get("type", "general"),
                "actor": intent.get("actor"),
                "director": intent.get("director"),
                "genre": intent.get("genre"),
                "year": intent.get("year")
            }
        }

    # ---------------------------------------------------------------------------
    # Real-Time Suggestions & Autocomplete
    # ---------------------------------------------------------------------------

    def get_suggestions(self, query: str, limit: int = 6) -> Dict[str, Any]:
        """
        Generates real-time suggestions as the user types:
        - Instant matching on title prefixes, fuzzy matches, and cast/directors
        - Highlighted HTML for matching characters
        - Typo correction indication
        """
        raw_query = (query or "").strip()
        norm_query = normalize_text(raw_query)
        phon_query = normalize_phonetic(raw_query)

        if not norm_query:
            return {"query": raw_query, "suggestions": [], "corrected_query": None, "spell_corrected": False}

        intent = self.parse_query_intent(raw_query)
        corrected_title, was_corrected = self.check_spelling_correction(raw_query, intent)
        effective_query = corrected_title if was_corrected else raw_query
        effective_norm = normalize_text(effective_query)
        effective_phon = normalize_phonetic(effective_query)
        query_words = effective_norm.split()

        scored_suggestions: List[Tuple[Dict[str, Any], float, str]] = []

        for movie in self.movies:
            score = self._score_movie(movie, raw_query, effective_norm, effective_phon, query_words, intent)
            if score > 0:
                match_field = "title"
                if effective_norm in movie["_norm_title"] or effective_phon in movie["_phon_title"]:
                    match_field = "title"
                elif any(effective_norm in d for d in movie["_norm_directors"]):
                    match_field = "director"
                elif any(effective_norm in a for a in movie["_norm_cast"][:5]):
                    match_field = "cast"
                elif any(effective_norm == g for g in movie["_norm_genres"]):
                    match_field = "genre"

                scored_suggestions.append((movie, score, match_field))

        scored_suggestions.sort(key=lambda x: x[1], reverse=True)

        suggestions = []
        for movie, score, match_field in scored_suggestions[:limit]:
            highlighted = highlight_matches(movie["title"], effective_query)
            rating = movie.get("vote_average", 0.0)
            suggestions.append({
                "id": movie["id"],
                "tmdb_id": movie.get("tmdb_id"),
                "title": movie["title"],
                "year": movie.get("year"),
                "rating": round(rating, 1) if rating else None,
                "poster_url": movie.get("poster_url"),
                "genres": movie.get("genres", [])[:3],
                "director": movie.get("director", ""),
                "match_field": match_field,
                "highlight_html": highlighted,
                "url": f"/movie/{movie['id']}"
            })

        return {
            "query": raw_query,
            "corrected_query": corrected_title if was_corrected else None,
            "spell_corrected": was_corrected,
            "suggestions": suggestions
        }


# Global singleton search engine
search_engine = SmartSearchEngine()
