"""
Recommendation Engine Module for CQ - Cinema Quotient
Implements:
- Module 5: Content-Based Filtering (TF-IDF + Cosine Similarity)
- Module 6: Collaborative Filtering (User-Item Rating Matrix & SVD / Similarity)
- Module 7: Hybrid Recommendation Engine (Weighted Score Combination)
- Module 8: Cold-Start Handling (Insufficient Ratings -> Genre Preferences & Popularity)
- Module 9: Explainable AI Rationale Generator ("Why recommended?")
"""

import math
import re
from collections import Counter

class MovieRecommendationEngine:
    def __init__(self, movies=None):
        self.refresh_catalog(movies or [])

    def refresh_catalog(self, movies):
        """Refresh recommendation features from the supplied canonical movie documents."""
        self.movies = []
        for source in movies:
            movie = dict(source)
            movie_id = movie.get("id", movie.get("tmdb_id"))
            if movie_id is None:
                continue
            movie["id"] = int(movie_id)
            genres = movie.get("genres") or []
            movie["genres"] = [
                value.get("name", "") if isinstance(value, dict) else str(value)
                for value in genres
            ]
            movie["genres"] = [genre for genre in movie["genres"] if genre]
            cast = movie.get("cast") or []
            movie["cast"] = [
                person.get("name", "") if isinstance(person, dict) else str(person)
                for person in cast
            ]
            movie["cast"] = [name for name in movie["cast"] if name]
            directors = movie.get("directors") or []
            movie["director"] = movie.get("director") or ", ".join(
                person.get("name", "") if isinstance(person, dict) else str(person)
                for person in directors
            )
            movie["overview"] = movie.get("overview") or ""
            movie["keywords"] = movie.get("keywords") or []
            movie["tmdb_rating"] = movie.get("tmdb_rating", movie.get("vote_average"))
            movie["vote_average"] = movie.get("vote_average", movie["tmdb_rating"])
            self.movies.append(movie)
        self.movie_map = {m["id"]: m for m in self.movies}
        self.movie_ids = [m["id"] for m in self.movies]
        self.id_to_idx = {m_id: idx for idx, m_id in enumerate(self.movie_ids)}
        self.idx_to_id = {idx: m_id for idx, m_id in enumerate(self.movie_ids)}
        self._build_content_based_model()

    def _build_content_based_model(self):
        """Module 5: Feature Construction + TF-IDF Vectorization + Cosine Similarity Matrix"""
        if not self.movies:
            self.tfidf_matrix = []
            self.content_sim_matrix = []
            return
        feature_strings = []
        for m in self.movies:
            genres_str = " ".join(m["genres"])
            cast_str = " ".join(m["cast"])
            keywords_str = " ".join(m["keywords"])
            text_soup = f"{m['title']} {genres_str} {m['overview']} {m['director']} {cast_str} {keywords_str}"
            feature_strings.append(text_soup.lower())
            
        # Tokenize and simple stopword removal
        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'is', 'are', 'was', 'were', 'for', 'in', 'of', 'to', 'with', 'on', 'at', 'by', 'this', 'that'}
        docs = [[w for w in re.findall(r'\b[a-z0-9]+\b', text) if w not in stopwords and len(w) > 2] for text in feature_strings]
        
        if not any(docs):
            self.tfidf_matrix = [{} for _ in docs]
            self.content_sim_matrix = [
                [0.0] * len(docs) for _ in docs
            ]
            return

        term_frequencies = []
        document_frequencies = Counter()
        for doc in docs:
            frequencies = Counter(doc)
            term_frequencies.append(frequencies)
            document_frequencies.update(frequencies.keys())

        idf = {
            word: math.log((len(docs) + 1) / (frequency + 1)) + 1
            for word, frequency in document_frequencies.items()
        }
        self.tfidf_matrix = []
        for frequencies in term_frequencies:
            weighted_terms = {
                word: count * idf[word]
                for word, count in frequencies.items()
            }
            norm = math.sqrt(sum(weight * weight for weight in weighted_terms.values()))
            if norm:
                weighted_terms = {
                    word: weight / norm for word, weight in weighted_terms.items()
                }
            self.tfidf_matrix.append(weighted_terms)

        self.content_sim_matrix = []
        for left in self.tfidf_matrix:
            row = []
            for right in self.tfidf_matrix:
                if len(left) > len(right):
                    left_terms, right_terms = right, left
                else:
                    left_terms, right_terms = left, right
                row.append(sum(
                    weight * right_terms.get(word, 0.0)
                    for word, weight in left_terms.items()
                ))
            self.content_sim_matrix.append(row)

    def get_similar_movies(self, movie_id, top_n=6):
        """Content-based retrieval of similar movies for a given movie page"""
        if movie_id not in self.id_to_idx:
            return []
        
        idx = self.id_to_idx[movie_id]
        sim_scores = list(enumerate(self.content_sim_matrix[idx]))
        sim_scores = sorted(sim_scores, key=lambda x: x[1], reverse=True)
        
        results = []
        for i, score in sim_scores:
            m_id = self.idx_to_id[i]
            if m_id == movie_id:
                continue
            movie_copy = dict(self.movie_map[m_id])
            movie_copy["similarity_score"] = round(float(score), 3)
            movie_copy["match_percentage"] = int(round(65 + score * 34))
            results.append(movie_copy)
            if len(results) >= top_n:
                break
        return results

    def _compute_content_scores_for_user(self, user_ratings):
        """Calculate aggregate content-based scores based on user's rated movies weighted by rating"""
        if not user_ratings:
            return [0.0] * len(self.movies)
        
        scores = [0.0] * len(self.movies)
        total_weight = 0.0
        
        for m_id, r in user_ratings.items():
            if m_id in self.id_to_idx:
                idx = self.id_to_idx[m_id]
                weight = (r - 2.5) / 2.5
                scores = [
                    score + similarity * weight
                    for score, similarity in zip(scores, self.content_sim_matrix[idx])
                ]
                total_weight += abs(weight)
                
        if total_weight > 0:
            scores = [score / total_weight for score in scores]
            
        min_s, max_s = min(scores), max(scores)
        if max_s > min_s:
            scores = [(score - min_s) / (max_s - min_s) for score in scores]
        else:
            scores = [0.0] * len(self.movies)
            
        return scores

    def _compute_collaborative_scores_for_user(self, user_ratings, all_ratings, active_user_id):
        """Collaborative scores from persistent user rating events."""
        ratings = [
            (event["user_id"], event["movie_id"], event["rating"])
            for event in all_ratings
            if event.get("user_id") != active_user_id and event.get("movie_id") in self.id_to_idx
        ]
        for m_id, r in user_ratings.items():
            if m_id in self.id_to_idx:
                ratings.append((active_user_id, m_id, r))
        if not ratings:
            return [0.0] * len(self.movies)

        rating_totals = {}
        rating_counts = {}
        for user_id, movie_id, rating in ratings:
            user_movie = (user_id, movie_id)
            rating_totals[user_movie] = rating_totals.get(user_movie, 0.0) + rating
            rating_counts[user_movie] = rating_counts.get(user_movie, 0) + 1

        movie_indices = {movie_id: idx for idx, movie_id in enumerate(self.movie_ids)}
        user_vectors = {}
        for (user_id, movie_id), total in rating_totals.items():
            vector = user_vectors.setdefault(user_id, [0.0] * len(self.movies))
            vector[movie_indices[movie_id]] = total / rating_counts[(user_id, movie_id)]

        active_vec = user_vectors.get(active_user_id)
        if active_vec is None:
            return [0.0] * len(self.movies)

        active_norm = math.sqrt(sum(rating * rating for rating in active_vec))
        similarities = {}
        if active_norm:
            for user_id, vector in user_vectors.items():
                if user_id == active_user_id:
                    continue
                vector_norm = math.sqrt(sum(rating * rating for rating in vector))
                if vector_norm:
                    dot_product = sum(
                        left * right for left, right in zip(vector, active_vec)
                    )
                    similarities[user_id] = dot_product / (vector_norm * active_norm)

        scores = [0.0] * len(self.movies)
        total_similarities = [0.0] * len(self.movies)
        for user_id, similarity in similarities.items():
            for idx, rating in enumerate(user_vectors[user_id]):
                if rating > 0:
                    scores[idx] += similarity * rating
                    total_similarities[idx] += similarity

        collab_scores = [
            score / total_similarity if total_similarity else 0.0
            for score, total_similarity in zip(scores, total_similarities)
        ]
        min_s, max_s = min(collab_scores), max(collab_scores)
        if max_s > min_s:
            collab_scores = [
                (score - min_s) / (max_s - min_s) for score in collab_scores
            ]
        else:
            collab_scores = [0.0] * len(self.movies)
            
        return collab_scores

    def get_hybrid_recommendations(
        self, user_ratings, favorite_genres=None, alpha=0.6, top_n=12,
        all_ratings=None, active_user_id=None
    ):
        """Module 7, 8, & 9: Hybrid recommendations with cold-start fallback & XAI explanations"""
        ratings_count = len(user_ratings)
        if not self.movies:
            return []
        
        if ratings_count < 2:
            return self._get_cold_start_recommendations(user_ratings, favorite_genres, top_n)
            
        content_scores = self._compute_content_scores_for_user(user_ratings)
        collab_scores = self._compute_collaborative_scores_for_user(
            user_ratings, all_ratings or [], active_user_id
        )
        
        hybrid_scores = [
            (alpha * content_score) + ((1 - alpha) * collab_score)
            for content_score, collab_score in zip(content_scores, collab_scores)
        ]
        
        rated_movie_ids = set(user_ratings.keys())
        
        recommendations = []
        for idx, score in enumerate(hybrid_scores):
            m_id = self.idx_to_id[idx]
            if m_id in rated_movie_ids:
                continue
                
            m = self.movie_map[m_id]
            
            c_score = float(content_scores[idx])
            cf_score = float(collab_scores[idx])
            h_score = float(score)
            
            explanation = self._generate_explanation(m, c_score, cf_score, user_ratings)
            match_pct = int(round(70 + h_score * 28))
            
            rec_item = dict(m)
            rec_item["hybrid_score"] = round(h_score, 4)
            rec_item["content_score"] = round(c_score, 4)
            rec_item["collab_score"] = round(cf_score, 4)
            rec_item["match_percentage"] = min(99, match_pct)
            rec_item["explanation"] = explanation
            recommendations.append(rec_item)
            
        recommendations = sorted(recommendations, key=lambda x: x["hybrid_score"], reverse=True)
        return recommendations[:top_n]

    def _get_cold_start_recommendations(self, user_ratings, favorite_genres, top_n=12):
        """Module 8: Cold-start genre matching and TMDB catalog ranking."""
        fav_genres = favorite_genres or ["Sci-Fi", "Action", "Drama"]
        rated_ids = set(user_ratings.keys())
        
        results = []
        for m in self.movies:
            if m["id"] in rated_ids:
                continue
            
            matching_genres = [g for g in m["genres"] if g in fav_genres]
            genre_bonus = len(matching_genres) * 0.35
            rating = m.get("tmdb_rating")
            popularity_score = (float(rating) / 10.0) if rating is not None else 0.0
            popularity_score += min(float(m.get("popularity") or 0) / 1000.0, 0.1)
            
            total_score = popularity_score + genre_bonus
            
            if matching_genres:
                explanation = f"Top choice for fans of {', '.join(matching_genres)}."
                match_pct = int(round(82 + len(matching_genres) * 5))
            else:
                explanation = f"Popular on TMDB ({m.get('tmdb_rating') or 'unrated'} / 10)."
                match_pct = int(round(75 + popularity_score * 20))
                
            item = dict(m)
            item["hybrid_score"] = round(total_score, 4)
            item["match_percentage"] = min(98, match_pct)
            item["explanation"] = explanation
            results.append(item)
            
        results = sorted(results, key=lambda x: x["hybrid_score"], reverse=True)
        return results[:top_n]

    def _generate_explanation(self, movie, content_score, collab_score, user_ratings):
        """Module 9: Explainable AI Rationale Generator"""
        if content_score > collab_score:
            highest_rated_id = max(user_ratings, key=user_ratings.get) if user_ratings else None
            matched_movie = self.movie_map.get(highest_rated_id) if highest_rated_id else None
            if matched_movie:
                genre = (matched_movie.get('genres') or ['movie'])[0]
                return f"Recommended because you loved {matched_movie['title']} ({genre})."
            genre = (movie.get('genres') or ['movie'])[0]
            director = movie.get('director') or 'its creative team'
            return f"Matches your preferred themes in {genre} and {director}."
        else:
            genre = (movie.get('genres') or ['movie'])[0]
            return f"Users with similar taste strongly recommended this {genre} title."

# Global singleton instance
engine = MovieRecommendationEngine()
