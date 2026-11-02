"""
Recommendation Engine Module for CQ - Cinema Quotient
Implements:
- Module 5: Content-Based Filtering (TF-IDF + Cosine Similarity)
- Module 6: Collaborative Filtering (User-Item Rating Matrix & SVD / Similarity)
- Module 7: Hybrid Recommendation Engine (Weighted Score Combination)
- Module 8: Cold-Start Handling (Insufficient Ratings -> Genre Preferences & Popularity)
- Module 9: Explainable AI Rationale Generator ("Why recommended?")
"""

import numpy as np
import pandas as pd
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
            self.content_sim_matrix = np.zeros((0, 0))
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
        
        vocab = {}
        for doc in docs:
            for word in doc:
                if word not in vocab:
                    vocab[word] = len(vocab)
        
        if not vocab:
            self.content_sim_matrix = np.zeros((len(self.movies), len(self.movies)))
            return
            
        tf_matrix = np.zeros((len(docs), len(vocab)))
        for i, doc in enumerate(docs):
            for word in doc:
                tf_matrix[i, vocab[word]] += 1
                
        doc_freq = np.sum(tf_matrix > 0, axis=0)
        idf = np.log((len(docs) + 1) / (doc_freq + 1)) + 1
        
        tfidf_matrix = tf_matrix * idf
        
        norms = np.linalg.norm(tfidf_matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1
        self.tfidf_matrix = tfidf_matrix / norms
        self.content_sim_matrix = np.dot(self.tfidf_matrix, self.tfidf_matrix.T)

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
            return np.zeros(len(self.movies))
        
        scores = np.zeros(len(self.movies))
        total_weight = 0.0
        
        for m_id, r in user_ratings.items():
            if m_id in self.id_to_idx:
                idx = self.id_to_idx[m_id]
                weight = (r - 2.5) / 2.5
                scores += self.content_sim_matrix[idx] * weight
                total_weight += abs(weight)
                
        if total_weight > 0:
            scores = scores / total_weight
            
        min_s, max_s = scores.min(), scores.max()
        if max_s > min_s:
            scores = (scores - min_s) / (max_s - min_s)
        else:
            scores = np.zeros(len(self.movies))
            
        return scores

    def _compute_collaborative_scores_for_user(self, user_ratings, all_ratings, active_user_id):
        """Collaborative scores from persistent user rating events."""
        ratings = [
            {"user_id": event["user_id"], "movie_id": event["movie_id"], "rating": event["rating"]}
            for event in all_ratings
            if event.get("user_id") != active_user_id and event.get("movie_id") in self.id_to_idx
        ]
        for m_id, r in user_ratings.items():
            if m_id in self.id_to_idx:
                ratings.append({
                "user_id": active_user_id,
                "movie_id": m_id,
                "rating": r
            })
        if not ratings:
            return np.zeros(len(self.movies))

        df = pd.DataFrame(ratings).groupby(
            ["user_id", "movie_id"], as_index=False
        )["rating"].mean()
        user_item_matrix = df.pivot(index='user_id', columns='movie_id', values='rating').fillna(0)
        
        for m_id in self.movie_ids:
            if m_id not in user_item_matrix.columns:
                user_item_matrix[m_id] = 0.0
                
        user_item_matrix = user_item_matrix[self.movie_ids]
        
        if active_user_id not in user_item_matrix.index:
            return np.zeros(len(self.movies))
            
        active_vec = user_item_matrix.loc[active_user_id].values
        
        active_vec_norm = np.linalg.norm(active_vec)
        if active_vec_norm == 0:
            user_sim = np.zeros(len(user_item_matrix))
        else:
            matrix_norms = np.linalg.norm(user_item_matrix.values, axis=1)
            matrix_norms[matrix_norms == 0] = 1
            user_sim = np.dot(user_item_matrix.values, active_vec) / (matrix_norms * active_vec_norm)
        
        
        sim_series = pd.Series(user_sim, index=user_item_matrix.index)
        sim_series.drop(active_user_id, errors='ignore', inplace=True)
        
        other_users_matrix = user_item_matrix.drop(active_user_id, errors='ignore')
        
        scores = np.zeros(len(self.movies))
        total_sim = np.zeros(len(self.movies))
        
        for idx, m_id in enumerate(self.movie_ids):
            movie_ratings = other_users_matrix[m_id]
            valid_mask = movie_ratings > 0
            if valid_mask.any():
                scores[idx] = np.dot(sim_series[valid_mask], movie_ratings[valid_mask])
                total_sim[idx] = sim_series[valid_mask].sum()
                
        with np.errstate(divide='ignore', invalid='ignore'):
            collab_scores = np.where(total_sim > 0, scores / total_sim, 0.0)
            
        min_s, max_s = collab_scores.min(), collab_scores.max()
        if max_s > min_s:
            collab_scores = (collab_scores - min_s) / (max_s - min_s)
        else:
            collab_scores = np.zeros(len(self.movies))
            
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
        
        hybrid_scores = (alpha * content_scores) + ((1 - alpha) * collab_scores)
        
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
